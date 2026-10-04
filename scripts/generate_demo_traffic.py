#!/usr/bin/env python3
"""Drive representative traffic through the gateway so the chargeback reports have something to show.

A freshly applied deployment has an empty ledger, so the workbook renders as a set of blank panels
and there is no way to tell a correct configuration from a broken one. This sends a deliberately
varied mix - several consumers, more than one model, short and long prompts, streaming and
non-streaming - so that every column the ledger records is exercised at least once and the resulting
report is worth looking at.

The mix matters. A hundred identical requests populate the row count and nothing else: every record
lands on one consumer, one model and one pricing tier, so a report that silently drops the consumer
dimension still looks right. Varying the traffic is what makes a wrong answer visible.

Consumers are passed as repeatable name=key pairs, and aliases each consumer may use are given per
consumer, because products restrict which models they entitle and a request outside that entitlement
is correctly rejected with 403.

Standard library only.

  python scripts/generate_demo_traffic.py --base-url https://<gw>.azure-api.net/openai/v1 \
      --consumer payments-api=<key>:chat,chat-fast,embed \
      --consumer research-agent=<key>:chat-fast,embed \
      --rounds 5
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from collections import Counter
from typing import Dict, List, Tuple

# A large prompt, so the ledger holds a spread of request sizes rather than one. Deliberately kept
# just under 10,000 characters: that is the most Azure AI Content Safety will assess in a single
# call, and a gateway with screening enabled refuses anything longer. Note the consequence - where
# content safety is on, prompts can never get big enough to reach a long-context rate card, so
# contextTier stays on "base" and the tiering logic is exercised only when screening is off.
LARGE_FILLER = (
    "Summarise the following operational notes. " + ("The service processed a routine batch. " * 230)
)

SHORT_PROMPTS = [
    "Reply with the single word: ok",
    "Name one Azure region.",
    "What is 2 + 2? Answer with digits only.",
    "Say 'done'.",
]


def post(base_url: str, key: str, path: str, payload: dict, timeout: int = 180
         ) -> Tuple[int, Dict[str, str]]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path, data=body, method="POST",
        headers={"Content-Type": "application/json", "api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
    except urllib.error.URLError as exc:
        raise RuntimeError("could not reach the gateway: {0}".format(exc.reason))


def parse_consumer(raw: str) -> Tuple[str, str, List[str]]:
    # name=key:alias,alias
    name, _, rest = raw.partition("=")
    key, _, aliases = rest.partition(":")
    if not name or not key:
        raise argparse.ArgumentTypeError(
            "expected name=key:alias,alias but got {0!r}".format(raw))
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()] or ["chat"]
    return name, key, alias_list


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--consumer", action="append", required=True, type=parse_consumer,
                    help="name=key:alias,alias - repeatable, one per consumer")
    ap.add_argument("--rounds", type=int, default=4,
                    help="How many times to run the full mix for each consumer.")
    ap.add_argument("--seed", type=int, default=7,
                    help="Fixed so a documented run can be reproduced exactly.")
    args = ap.parse_args()

    random.seed(args.seed)
    results: Counter = Counter()
    sent = 0

    for rnd in range(args.rounds):
        for name, key, aliases in args.consumer:
            chat_aliases = [a for a in aliases if a != "embed"]
            for alias in chat_aliases:
                # Short, ordinary request.
                status, _ = post(args.base_url, key, "/chat/completions", {
                    "model": alias,
                    "messages": [{"role": "user", "content": random.choice(SHORT_PROMPTS)}],
                    "max_tokens": 16,
                })
                results[(name, alias, "chat", status)] += 1
                sent += 1

                # Streaming, which the ledger records as unmeasured rather than as zero.
                status, _ = post(args.base_url, key, "/chat/completions", {
                    "model": alias,
                    "messages": [{"role": "user", "content": "Count to five."}],
                    "max_tokens": 32, "stream": True,
                })
                results[(name, alias, "stream", status)] += 1
                sent += 1

                # A large input, so prompt size varies across the ledger.
                if rnd == 0:
                    status, _ = post(args.base_url, key, "/chat/completions", {
                        "model": alias,
                        "messages": [{"role": "user", "content": LARGE_FILLER}],
                        "max_tokens": 24,
                    })
                    results[(name, alias, "large", status)] += 1
                    sent += 1

            if "embed" in aliases:
                status, _ = post(args.base_url, key, "/embeddings", {
                    "model": "embed",
                    "input": "quarterly reconciliation of gateway spend",
                })
                results[(name, "embed", "embed", status)] += 1
                sent += 1

            sys.stdout.write("\r  {0} requests sent".format(sent))
            sys.stdout.flush()

    sys.stdout.write("\n\n")
    print("  {0:<16} {1:<10} {2:<8} {3:>6} {4:>7}".format(
        "CONSUMER", "ALIAS", "KIND", "HTTP", "COUNT"))
    for (name, alias, kind, status), count in sorted(results.items()):
        print("  {0:<16} {1:<10} {2:<8} {3:>6} {4:>7}".format(name, alias, kind, status, count))

    ok = sum(c for (_, _, _, s), c in results.items() if s == 200)
    print("\n  {0} of {1} requests succeeded".format(ok, sent))
    print("  allow a couple of minutes for Application Insights to ingest before querying")
    # A 403 here is usually a product entitlement working as intended, so it is not a failure of the
    # generator; only a total absence of successful traffic is.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
