#!/usr/bin/env python3
"""Build the gateway's pricing map from the Azure Retail Prices API.

Why this exists
---------------
Every hand-maintained token price map drifts. Rates change, models are added, and sooner or later
somebody transcribes a rate quoted per thousand tokens into a field that expects a rate per million.
The chargeback numbers are then quietly wrong for a quarter. This script pulls published rates
straight from Azure so the map can be regenerated on a schedule and diffed in review.

What it deliberately does not do
--------------------------------
It reads *published retail* rates. It knows nothing about your enterprise agreement discount, your
reservations, or provisioned throughput amortisation, and it excludes batch, fine-tuned and
provisioned meters outright. That is the whole reason the accelerator allocates real Cost Management
spend by share of tokens instead of presenting this estimate as the bill - see
docs/cost-attribution.md.

Meter matching is heuristic because Azure's meter names are inconsistent (see scripts/
_pricing_meters.py for what that actually looks like). Run with --review the first time, and again
whenever you add a model.

Usage
-----
    # See what Azure actually publishes before committing to an alias
    python generate_pricing_map.py --region eastus --list-meters gpt-4o

    # Generate the map
    python generate_pricing_map.py --region eastus \\
        --alias chat=gpt-4o \\
        --alias chat-fast=gpt-4o-mini \\
        --alias embed=text-embedding-3-large \\
        --output pricing.auto.tfvars.json --review

    # Pin an exact model version rather than letting the script choose the newest
    python generate_pricing_map.py --region eastus --alias chat=gpt-4o-1120
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timezone
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from _pricing_meters import (  # noqa: E402
    BUCKETS,
    TARGET_UNIT,
    Rate,
    choose_version,
    classify,
    collect_rates,
    context_threshold,
    extract_deployment,
    is_excluded,
    match_model,
    tokenize,
)

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
API_VERSION = "2023-01-01-preview"

#: Azure renamed this service. It was "Cognitive Services"; Foundry model token meters now live under
#: "Foundry Models". Both are queried so the script keeps working either side of the rename and for
#: older meters that were never moved.
SERVICE_NAMES = ("Foundry Models", "Cognitive Services")


class PricingError(RuntimeError):
    pass


def fetch_all(filter_expr: str, currency: str) -> list[dict[str, Any]]:
    """Page through the Retail Prices API. It is anonymous, so no credentials are required."""
    items: list[dict[str, Any]] = []
    query = urllib.parse.urlencode(
        {"api-version": API_VERSION, "currencyCode": currency, "$filter": filter_expr}
    )
    url: str | None = f"{RETAIL_PRICES_URL}?{query}"

    while url:
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:  # pragma: no cover - network failure path
            detail = exc.read()[:400]
            raise PricingError(f"Retail Prices API returned {exc.code}: {detail!r}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network failure path
            raise PricingError(f"Could not reach the Retail Prices API: {exc.reason}") from exc

        items.extend(payload.get("Items", []))
        url = payload.get("NextPageLink")

    return items


def fetch_region(region: str, currency: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for service_name in SERVICE_NAMES:
        expr = (
            f"armRegionName eq '{region}' and serviceName eq '{service_name}' "
            "and priceType eq 'Consumption'"
        )
        items.extend(fetch_all(expr, currency))
    return items


def parse_alias(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--alias expects ALIAS=MODEL, for example chat=gpt-4o (got {value!r})"
        )
    alias, model = (part.strip() for part in value.split("=", 1))
    if not alias or not model:
        raise argparse.ArgumentTypeError(f"--alias expects ALIAS=MODEL (got {value!r})")
    return alias, model


def parse_context_threshold(value: str) -> tuple[str, int]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--context-threshold expects ALIAS=TOKENS, for example chat-long=272000 (got {value!r})"
        )
    alias, raw = (part.strip() for part in value.split("=", 1))
    if not alias:
        raise argparse.ArgumentTypeError(f"--context-threshold expects ALIAS=TOKENS (got {value!r})")
    try:
        tokens = int(raw.replace(",", "").replace("_", ""))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--context-threshold needs a whole number of tokens (got {raw!r})"
        ) from None
    if tokens <= 0:
        raise argparse.ArgumentTypeError("--context-threshold must be greater than zero")
    return alias, tokens


def _best_by_bucket(rates: list[Rate]) -> dict[str, Rate]:
    """Cheapest rate per bucket.

    Within one version, deployment type and context band there can still be duplicates, because the
    same rate is published under both the spaced and the hyphenated meter-name spelling. They agree
    on price, so the lowest is taken and the duplicate is not worth reporting.
    """
    best: dict[str, Rate] = {}
    for rate in rates:
        incumbent = best.get(rate.bucket)
        if incumbent is None or rate.per_million < incumbent.per_million:
            best[rate.bucket] = rate
    return best


def _rate_card(best: dict[str, Rate]) -> dict[str, Any]:
    """Shape a bucket map into the pricing-map rate fields."""
    card: dict[str, Any] = {
        "input": round(best["input"].per_million, 6),
        "output": round(best["output"].per_million, 6) if "output" in best else 0.0,
    }
    for bucket in ("cachedInput", "cacheWrite"):
        if bucket in best:
            card[bucket] = round(best[bucket].per_million, 6)
    return card


def build_entry(
    alias: str,
    model: str,
    items: list[dict[str, Any]],
    deployment_type: str,
    effective_date: str,
    region: str,
    review: bool,
    context_override: int | None = None,
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    """Resolve one alias to a pricing-map entry. Returns (entry, review_lines, failure_reason)."""
    rates, notes, near_misses = collect_rates(model, items, deployment_type)
    lines: list[str] = [f"! {note}" for note in notes]

    if not rates:
        if near_misses:
            lines.append(
                "near misses - meters starting with this model name but continuing with: "
                + ", ".join(sorted(near_misses))
            )
        return None, lines, "no token meters matched"

    version = choose_version(rates)
    selected = [rate for rate in rates if rate.version == version]

    # Context-length banding. Most models have none and every rate lands in the base card. For the
    # few that do, "short" is the base card and "long" becomes a tier the gateway selects at request
    # time from the prompt length.
    base_rates = [rate for rate in selected if rate.context in (None, "short")]
    long_rates = [rate for rate in selected if rate.context == "long"]

    best = _best_by_bucket(base_rates)

    if review:
        lines.append(f"version {version or '(unversioned)'}, deployment {deployment_type}")
        for bucket in BUCKETS:
            rate = best.get(bucket)
            if rate:
                lines.append(f"{bucket:12} {rate.per_million:>12.4f} /1M  <- {rate.meter_name}")
            else:
                lines.append(f"{bucket:12} {'-':>12}")

    if "input" not in best:
        return None, lines, f"no input-token meter for version {version} / {deployment_type}"

    if "output" not in best:
        # Embedding models genuinely have no output meter and bill only on what you send them.
        # Anything else with no output rate is a parsing failure, and shipping it would under-report
        # the model's cost by roughly the ratio of output to input price - typically four to eight
        # times. Refusing to emit the entry is the safe choice: an unpriced model is loudly flagged
        # by the gateway, whereas a half-priced one looks entirely plausible.
        looks_like_embedding = "embed" in model.lower()
        if not looks_like_embedding:
            if near_misses:
                lines.append(
                    "near misses - meters starting with this model name but continuing with: "
                    + ", ".join(sorted(near_misses))
                )
            return None, lines, (
                "found an input rate but no output rate, and this does not look like an embedding "
                "model. Run --list-meters "
                f"{model} to see the meters; if an output meter is listed as 'unrecognised', its "
                "abbreviation is missing from _QUALIFIER_WORD in scripts/_pricing_meters.py"
            )
        lines.append("no output meter - billing input only, which is correct for an embedding model")

    entry: dict[str, Any] = {"unit": TARGET_UNIT}
    entry.update(_rate_card(best))

    # A cached-input rate is a discount on the input rate, so it must be cheaper. If it is not, a
    # cached meter has almost certainly been misfiled as the plain input rate - and because the
    # cheapest candidate wins within a bucket, that would silently publish the discounted rate as the
    # headline one and under-report the model by roughly ten times.
    if "cachedInput" in entry and entry["cachedInput"] >= entry["input"]:
        return None, lines, (
            f"cached input rate ({entry['cachedInput']}) is not cheaper than the input rate "
            f"({entry['input']}), which means a meter has been misclassified. Run --list-meters "
            f"{model} and check the input and cachedInput rows"
        )

    # ---------------------------------------------------------------------------------------------
    # Long-context tier
    #
    # The retail API publishes the long-context rates but never the threshold that activates them, so
    # the threshold comes from CONTEXT_THRESHOLDS. When a model has long-context meters and no known
    # threshold, no tier is emitted and the operator is told: a guessed boundary prices real requests
    # on the wrong side of it, which is worse than a flat rate that is at least knowably approximate.
    # ---------------------------------------------------------------------------------------------
    if long_rates:
        threshold = context_threshold(model) if context_override is None else context_override
        long_best = _best_by_bucket(long_rates)

        if review:
            lines.append(f"long-context band, threshold {threshold if threshold else '(unknown)'}")
            for bucket in BUCKETS:
                rate = long_best.get(bucket)
                if rate:
                    lines.append(f"  {bucket:12} {rate.per_million:>12.4f} /1M  <- {rate.meter_name}")

        if threshold is None:
            lines.append(
                f"! {model} publishes long-context meters but no threshold is known for it. The entry "
                "prices every request at the short-context rate, which under-reports long prompts - "
                "for GPT-5.5 that is 2x on input and 1.5x on output. Supply the threshold with "
                f"--context-threshold {alias}=<prompt tokens>, or add it to CONTEXT_THRESHOLDS in "
                "scripts/_pricing_meters.py."
            )
        elif "input" not in long_best:
            lines.append(
                f"! {model} has long-context meters but no long-context input rate was parsed; no "
                "tier emitted. Run --list-meters to check."
            )
        else:
            tier: dict[str, Any] = {"name": "long", "minPromptTokens": threshold + 1}
            tier.update(_rate_card(long_best))

            # The long band must cost more than the short one, or the bands have been swapped
            # somewhere in parsing. Emitting them reversed would make long requests look cheap.
            if tier["input"] <= entry["input"]:
                lines.append(
                    f"! {model} long-context input rate ({tier['input']}) is not above the "
                    f"short-context rate ({entry['input']}); tier dropped as implausible."
                )
            else:
                entry["contextTiers"] = [tier]

    entry["effectiveDate"] = effective_date
    entry["source"] = {
        "model": model,
        "region": region,
        "version": version,
        "deployment": deployment_type,
        # The retail meter behind each rate. Cost Management reports spend by meter name, so this is
        # the column a reconciliation joins on when the question stops being "what did the gateway
        # estimate" and becomes "which invoice line is this". It is also the fastest way to check that
        # a surprising rate came from the meter you expected rather than a mis-parsed one.
        #
        # The ai-gateway module strips "source" before writing the named value, so this costs nothing
        # at the gateway - it is provenance for the generated file and for whoever reviews it.
        "meters": {bucket: rate.meter_name for bucket, rate in sorted(best.items())},
    }
    return entry, lines, None


def list_meters(model: str, items: list[dict[str, Any]], region: str) -> int:
    model_tokens = tokenize(model)
    rows: list[tuple[str, str, str, float, str]] = []

    for item in items:
        meter_name = item.get("meterName", "")
        meter_tokens = tokenize(meter_name)
        status, tail = match_model(meter_tokens, model_tokens)
        if tail is None:
            continue

        if status == "unrecognised":
            # Either a longer model name or a missing abbreviation. Shown so the operator can tell
            # which, because only one of those two is a bug.
            label = f"?{tail[0]}"[:12]
        elif is_excluded(tail, meter_tokens):
            label = "excluded"
        else:
            label = classify(tail) or "unclassified"
        rows.append(
            (
                label,
                extract_deployment(tail) or "-",
                item.get("unitOfMeasure", "?"),
                float(item.get("retailPrice") or 0.0),
                meter_name,
            )
        )

    if not rows:
        print(
            f"No meters matched {model!r} in {region}.\n"
            "Model names in meters follow the publisher's naming, e.g. gpt-4o, gpt-4o-mini, "
            "text-embedding-3-large, Phi-4-Mini.",
            file=sys.stderr,
        )
        return 1

    print(f"{len(rows)} meter(s) matching {model!r} in {region}:\n")
    for label, deployment, unit, price, meter_name in sorted(rows, key=lambda row: row[4]):
        print(f"  [{label:<12}] {deployment:<9} {price:>12.8f} per {unit:<5} {meter_name}")
    print(
        "\n'excluded' covers batch, fine-tuned, provisioned, audio, image and realtime meters, which "
        "are\nbilled on different terms from the chat and embedding traffic the gateway measures.\n"
        "\nA '?word' label means the meter starts with this model name but then continues with an\n"
        "unrecognised word. Usually that word is a longer model name - '?mini' under gpt-4o is\n"
        "gpt-4o-mini and is correctly skipped. If it is instead an abbreviation of input or output,\n"
        "add it to _QUALIFIER_WORD in scripts/_pricing_meters.py, or that rate is being lost.",
        file=sys.stderr,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_pricing_map.py",
        description="Generate the AI gateway pricing map from published Azure retail rates.",
    )
    parser.add_argument("--region", required=True, help="ARM region name, for example eastus.")
    parser.add_argument(
        "--alias",
        action="append",
        type=parse_alias,
        default=[],
        metavar="ALIAS=MODEL",
        help="Gateway alias mapped to a publisher model name. Repeatable. "
        "Append a version to pin it, e.g. chat=gpt-4o-1120.",
    )
    parser.add_argument(
        "--deployment-type",
        choices=("global", "regional", "datazone", "any"),
        default="global",
        help="Match the deployment type you actually provisioned. Rates differ by roughly 10 percent "
        "between them. Default: global.",
    )
    parser.add_argument(
        "--context-threshold",
        action="append",
        type=parse_context_threshold,
        default=[],
        metavar="ALIAS=TOKENS",
        help="Prompt-token count above which a model bills at its long-context rate. Repeatable. "
        "Only needed for a model whose threshold is not already in CONTEXT_THRESHOLDS; the retail "
        "API publishes the long-context rates but never the threshold itself.",
    )
    parser.add_argument("--currency", default="USD", help="Currency code. Default USD.")
    parser.add_argument("--output", help="Write to a file. Omit to print to stdout.")
    parser.add_argument(
        "--format",
        choices=("tfvars", "json"),
        default="tfvars",
        help="tfvars wraps the map in a pricing_map key, suitable for a .auto.tfvars.json file.",
    )
    parser.add_argument(
        "--effective-date",
        default=date.today().isoformat(),
        help="Stamped onto every entry; the workbook surfaces it as the rate card's age.",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Print the meter chosen for each rate. Use this the first time and whenever adding a model.",
    )
    parser.add_argument(
        "--list-meters",
        metavar="MODEL",
        help="List every meter matching a model and exit, without building a map.",
    )
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Exit non-zero if any alias could not be priced. Use this in CI.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        items = fetch_region(args.region, args.currency)
    except PricingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not items:
        print(
            f"error: no model meters found in region {args.region!r}. Check the region name against "
            "`az account list-locations --query \"[].name\" -o tsv`.",
            file=sys.stderr,
        )
        return 2

    if args.list_meters:
        return list_meters(args.list_meters, items, args.region)

    if not args.alias:
        print("error: at least one --alias ALIAS=MODEL is required.", file=sys.stderr)
        return 2

    pricing_map: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    context_overrides = dict(args.context_threshold)

    for alias, model in args.alias:
        entry, lines, failure = build_entry(
            alias,
            model,
            items,
            args.deployment_type,
            args.effective_date,
            args.region,
            args.review,
            context_overrides.get(alias),
        )

        if args.review or failure:
            print(f"\n{alias}  <-  {model}", file=sys.stderr)
            for line in lines or ["(nothing matched)"]:
                print(f"  {line}", file=sys.stderr)

        if entry is None:
            failures.append(f"{alias} ({model}): {failure}")
            continue
        pricing_map[alias] = entry

    if failures:
        print("\nwarning: no rates produced for:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nAn alias with no rate is reported by the gateway with a cost of -1 ('unpriced') and "
            "raises\nthe unpriced_models alert. That is intended: a missing rate should be loud, not "
            "silently zero.\nRun --list-meters MODEL to see what Azure publishes for it.",
            file=sys.stderr,
        )

    if not pricing_map:
        print("error: no rates could be resolved for any alias.", file=sys.stderr)
        return 1

    document: Any = {"pricing_map": pricing_map} if args.format == "tfvars" else pricing_map
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        print(
            f"\nWrote {len(pricing_map)} alias(es) to {args.output}. Read the diff before committing: "
            "meter matching is heuristic.",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(rendered)

    return 1 if (failures and args.fail_on_incomplete) else 0


if __name__ == "__main__":
    raise SystemExit(main())
