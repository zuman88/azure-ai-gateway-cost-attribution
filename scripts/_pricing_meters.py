"""Meter-name parsing for the Azure Retail Prices API.

Kept in its own module so the parsing rules can be unit-tested without touching the network. See
tests/test_pricing_meters.py.

Azure's token meter names are written for billing pipelines, not for people, and they are not
internally consistent. All of the following are real meters for the same model family in one region:

    gpt 4o 0806 Inp Data Zone Tokens
    gpt-4o-0806-Inp-glbl Tokens
    gpt 4o 1120 cached Inp regnl Tokens
    gpt 4o mini0718 BatchOutp DataZone Tokens
    gpt 4o mini dev ft cchd inpt glbl Tokens

So: whitespace and hyphens are interchangeable separators, words are abbreviated unpredictably
("Inp"/"Input"/"inpt", "cached"/"cchd", "glbl"/"global"), and adjacent words are sometimes run
together ("mini0718", "BatchOutp", "DataZone"). The parser below normalises all of that into a token
list and then reasons about position, which is the only stable signal in the data.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, NamedTuple

TARGET_UNIT = 1_000_000

#: Buckets the gateway's cost formula understands. Order matters for display only.
BUCKETS = ("input", "cachedInput", "cacheWrite", "output")

#: Deployment types, mapped to the abbreviations Azure uses for them in meter names.
DEPLOYMENT_MARKERS: dict[str, tuple[str, ...]] = {
    "global": ("glbl", "global"),
    "regional": ("regnl", "regional"),
    "datazone": ("datazone", "dzone", "dz", "dzn"),
}

#: Tokens that may legitimately follow a model name. Anything else means we have matched a prefix of
#: a *different, longer* model name - "gpt 4o mini" is not "gpt 4o" - and the candidate is rejected.
#: This is the rule that stops gpt-4o from silently absorbing gpt-4o-mini's rates.
#: Azure's abbreviations are not consistent even within one model family: input appears as "Inp",
#: "Inpt" and "Input", and output as "Outp", "Outpt", "outpt", "Output", "opt" and "out". Every
#: spelling has to be listed, because an unlisted one causes the meter to be dropped silently. That
#: is not hypothetical - "outpt" was missing from an earlier revision of this list, which dropped
#: GPT-5's output rate and would have under-reported its cost by around 80 percent. The
#: "unrecognised" near-miss reporting below exists so that failure mode is visible instead of silent.
_QUALIFIER_WORD = (
    r"batch|cached|cach|cchd|chd|cd"
    r"|inpt|input|inp|in"
    r"|output|outpt|outp|otpt|opt|out"
    r"|compl|completion|gen|generated"
    r"|glbl|global|regnl|regional|datazone|dzone|dzn|dz|data|zone"
    r"|tokens|token|tkns|tks"
    r"|ft|finetuned|finetune|dev|prov|provisioned|ptu|mngd|managed"
    r"|flex|prem|premium|standard|std|hosted|cache|write|creation|read|new"
    r"|shortco|longco|co|pp|priority"
)
QUALIFIER_RE = re.compile(rf"^(?:{_QUALIFIER_WORD})+$", re.I)

#: Meters that are real, priced, and absolutely not what a chat completion through the gateway costs.
#: Batch is a different (cheaper) submission path; fine-tuned and provisioned deployments are billed
#: on entirely different terms; audio, image and realtime meters are different modalities that the
#: token ledger does not measure.
EXCLUDED_TOKENS = frozenset(
    {
        "batch", "batchinp", "batchoutp",
        "ft", "finetuned", "finetune",
        "prov", "provisioned", "ptu",
        "aud", "audio", "rt", "realtime", "tcrb", "transcribe", "tts",
        "img", "image", "vision", "video", "speech", "ocr", "parser",
        "hosted", "hosting", "training", "mngd", "managed",
        # Priority processing is a separate, dearer submission path - GPT-5.5 "ShortCo PP" is 2.5x
        # the standard rate. Mixing it into the standard rate card would overstate every request.
        "pp", "priority",
    }
)

#: Context-length pricing bands. Some models charge more for long inputs, and Azure publishes this as
#: separate "ShortCo"/"LongCo" meters.
CONTEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "short": ("shortco",),
    "long": ("longco",),
}

#: Prompt-token counts at which a model moves into its long-context band.
#:
#: This is NOT published in the retail prices API - the meters say "LongCo" but never say how long is
#: long - so it has to be carried here, sourced from the Azure OpenAI pricing page. A model with
#: long-context meters but no entry here produces a warning and no tier, rather than a guessed
#: threshold: pricing the wrong side of a boundary is worse than flagging that the boundary is
#: unknown.
#:
#: Verified against Azure's own worked example for GPT-5.5 Data Zone Batch - Long context:
#:   272,001 input @ $5.50/1M + 1,000 output @ $24.75/1M = $1.5207555, their published figure.
#: That also confirms the band is not marginal - the whole request reprices.
CONTEXT_THRESHOLDS: dict[str, int] = {
    "gpt-5.5": 272_000,
    "gpt-5-5": 272_000,
    "5.5": 272_000,
}


class Rate(NamedTuple):
    """One resolved rate, normalised to a price per million tokens."""

    bucket: str
    per_million: float
    meter_name: str
    version: str | None
    deployment: str | None
    #: "short", "long", or None when the model has no context-length banding at all. None and "short"
    #: both belong in the base rate card; only "long" becomes a tier.
    context: str | None = None


def tokenize(value: str) -> list[str]:
    """Split a meter or model name into comparable lowercase tokens.

    Separators (spaces, hyphens, dots) are dropped, and runs of letters are split from runs of digits
    so that "mini0718" and "mini 0718" both become ["mini", "0718"]. "gpt-4o" becomes
    ["gpt", "4", "o"] - which looks odd, but the model name on the other side of the comparison is
    tokenized identically, so prefix matching still holds.
    """
    tokens: list[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", value.lower()):
        if chunk:
            tokens.extend(re.findall(r"[a-z]+|[0-9]+", chunk))
    return tokens


def is_version(token: str) -> bool:
    """Is this token a model version stamp such as 0806 or 20250514?

    Three digits is the threshold, and it is deliberate. A one- or two-digit run immediately after a
    model name is almost always the rest of the model's version number - "GPT 5.1" tokenizes to
    ["gpt", "5", "1"], so treating "1" as a qualifier would make gpt-5 quietly inherit gpt-5.1's
    rates. Requiring three digits keeps those families apart.
    """
    return token.isdigit() and len(token) >= 3


def match_model(
    meter_tokens: list[str], model_tokens: list[str]
) -> tuple[str, list[str] | None]:
    """Classify a meter against a model name.

    Returns one of:

    ``("match", tail)``
        The meter's tokens begin with the model's tokens and the next token is a recognised
        qualifier or version stamp, so the remainder describes this model's billing dimensions.

    ``("unrecognised", tail)``
        The model prefix matched, but the following token is a word we do not know. This is
        ambiguous: it is either a longer model name ("gpt 4o **mini** ...", which must be rejected)
        or an abbreviation missing from the vocabulary (which is a bug that silently loses a rate).
        Callers surface these as near misses rather than discarding them quietly.

    ``("no", None)``
        Not this model.
    """
    if len(meter_tokens) < len(model_tokens):
        return "no", None
    if meter_tokens[: len(model_tokens)] != model_tokens:
        return "no", None

    tail = meter_tokens[len(model_tokens) :]
    if not tail:
        return "no", None
    if is_version(tail[0]) or QUALIFIER_RE.match(tail[0]):
        return "match", tail
    return "unrecognised", tail


def split_model(meter_tokens: list[str], model_tokens: list[str]) -> list[str] | None:
    """Return the tokens after the model name, or None if this meter is not usable for this model."""
    status, tail = match_model(meter_tokens, model_tokens)
    return tail if status == "match" else None


def extract_version(tail: list[str]) -> str | None:
    for token in tail:
        if is_version(token):
            return token
    return None


def extract_deployment(tail: list[str]) -> str | None:
    """Identify global / regional / data-zone from the tail.

    Checked as substrings because Azure runs words together ("DataZone", "BatchOutp"). "dz" is tested
    last and only as a whole token, since it is short enough to appear inside unrelated words.
    """
    joined = "".join(tail)
    for deployment, markers in DEPLOYMENT_MARKERS.items():
        for marker in markers:
            if len(marker) <= 2:
                if marker in tail:
                    return deployment
            elif marker in joined:
                return deployment
    return None


def extract_context(tail: list[str]) -> str | None:
    """Identify the context-length band from the tail, if the model has one.

    Matched against the joined tail because Azure runs the words together as often as not. Returns
    None for the overwhelming majority of models, which are priced at one flat rate.
    """
    joined = "".join(tail)
    for band, markers in CONTEXT_MARKERS.items():
        if any(marker in joined for marker in markers):
            return band
    return None


def context_threshold(model: str) -> int | None:
    """Prompt-token count at which `model` enters its long-context band, if known.

    Looked up on the normalised model name so that "GPT-5.5", "gpt-5-5" and "gpt 5 5" all resolve.
    """
    key = "-".join(tokenize(model))
    for candidate, threshold in CONTEXT_THRESHOLDS.items():
        if "-".join(tokenize(candidate)) == key:
            return threshold
    return None


def classify(tail: list[str]) -> str | None:
    """Sort a meter into an input / cached-input / cache-write / output bucket.

    Order is load-bearing. Cache writes must be tested before cache reads, and both must be tested
    before plain input, because every cached-input meter also contains the word "Inp".
    """
    joined = "".join(tail)

    # "cd" and "chd" are further abbreviations of cached, and only ever appear as whole tokens, so
    # they are matched against the token list rather than the joined string to avoid firing on the
    # "cd" inside an unrelated word.
    is_cached = "cach" in joined or "cchd" in joined or "cd" in tail or "chd" in tail
    if is_cached and ("write" in joined or "creation" in joined):
        return "cacheWrite"
    if is_cached:
        return "cachedInput"

    # "opt" is Azure's abbreviation for output in the fine-tune meters; "out"/"outp" elsewhere.
    if re.search(r"outp|output|\bopt\b", joined) or "opt" in tail or "out" in tail:
        return "output"
    if "inp" in joined or "input" in joined or "inpt" in joined:
        return "input"

    # Embedding models have a single undirected token meter - "text-embedding-3-large-glbl Tokens",
    # "embedding-ada-regional Tokens" - because they only ever bill on what you send them. Treating
    # that as the input rate is correct, and the gateway then prices their (nonexistent) output at
    # zero rather than leaving the model unpriced.
    if "token" in joined:
        return "input"
    return None


def is_excluded(tail: list[str], meter_tokens: list[str]) -> bool:
    if EXCLUDED_TOKENS.intersection(tail):
        return True
    # Catch modality markers that appear inside the model portion, e.g. gpt-4o-aud-0603.
    return bool(EXCLUDED_TOKENS.intersection(meter_tokens) & {"aud", "rt", "tcrb", "img", "ocr"})


def normalise_to_million(price: float, unit_of_measure: str) -> float | None:
    """Convert a published rate to a rate per million tokens.

    Azure publishes token meters per 1K, 10K, 100K or 1M depending on the model and on when the meter
    was created. Getting this wrong by a factor of a thousand is the most common way a chargeback
    model ends up embarrassing somebody, so an unrecognised unit returns None rather than a guess.
    """
    match = re.match(r"\s*([\d,.]+)\s*([KMkm]?)", unit_of_measure or "")
    if not match:
        return None
    try:
        quantity = float(match.group(1).replace(",", ""))
    except ValueError:
        return None

    multiplier = {"": 1, "k": 1_000, "K": 1_000, "m": 1_000_000, "M": 1_000_000}[match.group(2)]
    tokens_per_unit = quantity * multiplier
    if tokens_per_unit <= 0:
        return None
    return price * (TARGET_UNIT / tokens_per_unit)


def collect_rates(
    model: str,
    items: Iterable[dict[str, Any]],
    deployment_type: str = "global",
) -> tuple[list[Rate], list[str], set[str]]:
    """Return every usable rate for a model.

    Also returns notes about anything deliberately skipped, and the set of unrecognised tokens that
    immediately followed the model name. Those near misses are usually longer model names - "mini"
    after "gpt 4o" - but they are the only place a missing abbreviation can show up, so they are
    reported rather than swallowed.
    """
    model_tokens = tokenize(model)
    items = list(items)

    rates, notes, near_misses = _collect(model_tokens, items, deployment_type)

    # Azure does not always put the family name in the meter. GPT-5.5's meters are named "5.5 ShortCo
    # inp Gl 1M Tokens" with no "gpt" anywhere, so a perfectly reasonable --alias chat=gpt-5.5 matches
    # nothing at all. Rather than make the caller discover that by reading meter dumps, retry without
    # the prefix and say so.
    if not rates and len(model_tokens) > 1 and model_tokens[0] == "gpt":
        stripped = model_tokens[1:]
        retry_rates, retry_notes, retry_near = _collect(stripped, items, deployment_type)
        if retry_rates:
            notes.append(
                f"no meters are named {model!r}; matched {'.'.join(stripped)!r} instead, because "
                "Azure omits the family prefix from this model's meter names"
            )
            return retry_rates, notes + retry_notes, retry_near

    return rates, notes, near_misses


def _collect(
    model_tokens: list[str],
    items: Iterable[dict[str, Any]],
    deployment_type: str,
) -> tuple[list[Rate], list[str], set[str]]:
    """Single matching pass for an already-tokenized model name."""
    rates: list[Rate] = []
    notes: list[str] = []
    near_misses: set[str] = set()

    for item in items:
        meter_name = item.get("meterName", "")
        meter_tokens = tokenize(meter_name)
        status, tail = match_model(meter_tokens, model_tokens)
        if status == "unrecognised" and tail:
            near_misses.add(tail[0])
            continue
        if tail is None or is_excluded(tail, meter_tokens):
            continue

        bucket = classify(tail)
        if bucket is None:
            continue

        deployment = extract_deployment(tail)
        if deployment_type != "any" and deployment is not None and deployment != deployment_type:
            continue

        price = item.get("retailPrice")
        if not isinstance(price, (int, float)) or price <= 0:
            continue

        per_million = normalise_to_million(float(price), item.get("unitOfMeasure", ""))
        if per_million is None:
            notes.append(f"skipped {meter_name!r}: unrecognised unit {item.get('unitOfMeasure')!r}")
            continue

        rates.append(
            Rate(bucket, per_million, meter_name, extract_version(tail), deployment, extract_context(tail))
        )

    return rates, notes, near_misses


def choose_version(rates: list[Rate]) -> str | None:
    """Pick which model version's rates to publish when several are on offer.

    Preference is for a version that prices both input and output - a version with only a cached-input
    meter is an artefact, not a usable rate card - and then for the highest version stamp, which is
    the most recent release. Callers can bypass all of this by naming the version explicitly in the
    alias, e.g. --alias chat=gpt-4o-1120.
    """
    versions: dict[str | None, set[str]] = {}
    for rate in rates:
        versions.setdefault(rate.version, set()).add(rate.bucket)

    if not versions:
        return None

    def sort_key(version: str | None) -> tuple[int, int]:
        buckets = versions[version]
        complete = 1 if {"input", "output"} <= buckets else 0
        stamp = int(version) if version and version.isdigit() else -1
        return (complete, stamp)

    return max(versions, key=sort_key)
