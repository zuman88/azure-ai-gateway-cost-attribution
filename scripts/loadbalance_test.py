#!/usr/bin/env python3
"""Exercise a gateway's backend pools and report how traffic was actually distributed.

smoke_test.py answers "does the gateway work". This answers "does the routing do what the
Terraform said it would": whether a weighted pool splits traffic in the configured ratio, and
whether a priority pool keeps all traffic on the primary until something goes wrong.

Distribution is measured from the x-ai-region response header, which carries the region that
actually served the request. x-ai-deployment and x-ai-pool are resolved before routing, so every
member of a pool reports the same values and neither can tell two backends apart; region is the
only client-visible signal that distinguishes them. The gateway deletes Foundry's own x-ms-region
header to avoid leaking backend detail, so a gateway built before x-ai-region existed cannot be
measured this way, and the script says so rather than reporting a misleading 100% share.

Standard library only, so it runs anywhere Terraform does.

  python scripts/loadbalance_test.py --base-url https://<gw>.azure-api.net/openai/v1 \
      --api-key <key> --alias chat-fast --requests 100 --expect primary=70 --expect secondary=30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Dict, List, Optional, Tuple


def call(base_url: str, api_key: str, alias: str, timeout: int = 120
         ) -> Tuple[int, Dict[str, str], Optional[dict]]:
    payload = json.dumps({
        "model": alias,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 5,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", "api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        return exc.code, headers, None
    except urllib.error.URLError as exc:
        raise RuntimeError("could not reach the gateway: {0}".format(exc.reason))


def run(base_url: str, api_key: str, alias: str, n: int, delay: float) -> List[Dict[str, str]]:
    observations: List[Dict[str, str]] = []
    for i in range(n):
        status, headers, _ = call(base_url, api_key, alias)
        observations.append({
            "status": str(status),
            "deployment": headers.get("x-ai-deployment", "-"),
            "pool": headers.get("x-ai-pool", "-"),
            "region": headers.get("x-ai-region", "-"),
            "tokens": headers.get("x-ai-total-tokens", "-"),
            "consumer": headers.get("x-ai-consumer", "-"),
        })
        sys.stdout.write("\r  {0}/{1} requests".format(i + 1, n))
        sys.stdout.flush()
        if delay:
            time.sleep(delay)
    sys.stdout.write("\n")
    return observations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--alias", required=True)
    ap.add_argument("--requests", type=int, default=100)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--expect", action="append", default=[],
                    help="Expected share, e.g. primary=70. Repeatable.")
    ap.add_argument("--tolerance", type=float, default=12.0,
                    help="Percentage points a share may differ from --expect before failing.")
    ap.add_argument("--json-out", help="Write the raw observations here.")
    args = ap.parse_args()

    print("gateway: {0}".format(args.base_url))
    print("alias:   {0}".format(args.alias))
    print("sending {0} request(s)".format(args.requests))

    obs = run(args.base_url, args.api_key, args.alias, args.requests, args.delay)

    ok = [o for o in obs if o["status"] == "200"]
    failed = len(obs) - len(ok)
    if not ok:
        print("\nevery request failed; nothing to measure")
        codes = Counter(o["status"] for o in obs)
        for code, count in codes.most_common():
            print("  HTTP {0}: {1}".format(code, count))
        return 1

    regions = Counter(o["region"] for o in ok)
    deployments = Counter(o["deployment"] for o in ok)
    pools = Counter(o["pool"] for o in ok)

    # Without a region there is exactly one bucket, and every share is 100% by construction. That
    # would quietly "pass" a 70/30 expectation that was never actually measured, so refuse instead.
    if set(regions) <= {"-", "", "unknown"}:
        print("\n  no x-ai-region on any response, so the serving backend cannot be identified")
        print("  distribution is unmeasurable from the client; re-apply so the gateway policy")
        print("  emits x-ai-region, or attribute from the chargeback ledger instead")
        return 1

    print("\n  {0} succeeded, {1} failed".format(len(ok), failed))
    print("  pool(s):      {0}".format(", ".join(sorted(pools))))
    print("  deployment(s):{0}".format(", ".join(sorted(deployments))))
    print("\n  distribution by Foundry region")
    print("  {0:<22} {1:>7} {2:>9}".format("REGION", "COUNT", "SHARE"))
    for region, count in regions.most_common():
        print("  {0:<22} {1:>7} {2:>8.1f}%".format(region, count, 100.0 * count / len(ok)))

    # A priority pool looks broken when it is working. Its whole purpose is to send everything to the
    # first region and spill to the second only when the first stops accepting work, so a run that
    # provokes any throttling reports a split - and a flat percentage cannot distinguish "failover
    # engaged correctly" from "weights are wrong". Ordering can: if no secondary request precedes the
    # first failure, preference held and the spill was a response to the primary, not a round robin.
    errors = [i for i, o in enumerate(obs) if o["status"] != "200"]
    if errors and len(regions) > 1:
        first_error = errors[0]
        busiest = regions.most_common(1)[0][0]
        before = [o for o in obs[:first_error] if o["status"] == "200"]
        others_before = sum(1 for o in before if o["region"] != busiest)
        others_after = sum(1 for o in obs[first_error:]
                           if o["status"] == "200" and o["region"] != busiest)

        print("\n  failover timeline")
        print("    first failure at request {0} of {1}".format(first_error + 1, len(obs)))
        print("    requests to {0} before that: {1}".format(busiest, len(before) - others_before))
        print("    requests elsewhere before that: {0}".format(others_before))
        print("    requests elsewhere after that:  {0}".format(others_after))
        if others_before == 0 and others_after > 0:
            print("    -> consistent with priority failover: the preferred region took everything")
            print("       until it started refusing, and only then did traffic move")
        elif others_before > 0:
            print("    -> traffic reached more than one region before any failure, which is weighted")
            print("       load balancing rather than priority failover")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"observations": obs,
                       "regions": dict(regions),
                       "deployments": dict(deployments)}, handle, indent=2)
        print("\n  raw observations written to {0}".format(args.json_out))

    exit_code = 0
    if args.expect:
        print("\n  expected distribution (tolerance +/-{0:.0f} points)".format(args.tolerance))
        for item in args.expect:
            name, _, target = item.partition("=")
            target_pct = float(target)
            # Regions arrive in display form ("East US 2"), while callers think in resource form
            # ("eastus2"). Compare with spaces and case removed so either spelling works. Regions are
            # checked first and deployments only as a fallback, because counting a name that happens
            # to match in both would double it and inflate the share past 100%.
            def norm(value: str) -> str:
                return value.replace(" ", "").lower()

            wanted = norm(name)
            count = sum(c for k, c in regions.items() if wanted in norm(k))
            if not count:
                count = sum(c for k, c in deployments.items() if wanted in norm(k))
            actual = 100.0 * count / len(ok) if count else 0.0
            delta = abs(actual - target_pct)
            verdict = "PASS" if delta <= args.tolerance else "FAIL"
            if verdict == "FAIL":
                exit_code = 1
            print("  {0:<22} expected {1:>5.1f}%  actual {2:>5.1f}%  {3}".format(
                name, target_pct, actual, verdict))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
