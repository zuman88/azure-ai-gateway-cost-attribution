# ADR-0008: Context-length pricing tiers

- **Status:** Accepted
- **Date:** 2026-10-04
- **Related:** ADR-0004 (the ratio-allocation chargeback model these rates feed), ADR-0005 (the ledger trace that carries `contextTier`)

## Context

The cost model assumed one rate card per model alias: an input rate, an output rate, and optionally
cached and cache-write rates. That assumption held for every model the accelerator was built against.

It is no longer true. Azure now prices some models by **input length**. GPT-5.5 is the live example,
published as separate `ShortCo` and `LongCo` meters. For Global Standard, per 1M tokens:

| Band | Input | Cached input | Output |
|---|---|---|---|
| Short context | $5.00 | $0.50 | $30.00 |
| **Long context** | **$10.00** | **$1.00** | **$45.00** |

A flat-rate entry priced at the short-context figures under-reports a long-context request by **2× on
input and 1.5× on output**. This is the failure mode the rest of the cost design works hardest to
avoid: the resulting number is plausible, internally consistent, and wrong. Nothing about a cost
report containing it looks suspicious.

The same gap existed in the hand-built gateway this accelerator replaces, so it is not a regression —
but it is a correctness defect in the module's central claim.

Two properties of this pricing are easy to assume incorrectly, and assuming either one wrongly
produces a materially wrong bill. Both were verified against Azure's own published worked example
rather than inferred:

**The threshold is measured on total prompt tokens, including cached ones.** Caching changes what a
token costs, not whether the model had to carry it in context.

**The pricing is not marginal.** Crossing the threshold reprices the *entire* request at the higher
rate. There is no cheap first tranche. Azure's example for Data Zone Batch long context gives
272,001 input and 1,000 output tokens as $1.5207555, and:

```
272001/1e6 × $5.50  +  1000/1e6 × $24.75  =  $1.4960055 + $0.02475  =  $1.5207555   ✓
```

A marginal interpretation would have produced roughly half that on the input side. The arithmetic
closing exactly is what makes this a verified fact rather than a reasonable reading.

## Decision

Add an optional `contextTiers` array to a `pricing_map` entry. The base entry carries the lowest
band; each tier overrides it above a `minPromptTokens` threshold:

```json
"gpt-5-5": {
  "unit": 1000000,
  "input": 5.0, "cachedInput": 0.5, "output": 30.0,
  "contextTiers": [
    { "name": "long", "minPromptTokens": 272001,
      "input": 10.0, "cachedInput": 1.0, "output": 45.0 }
  ]
}
```

The gateway resolves the applicable card once per request from `promptTokens`, selecting the matching
tier with the highest threshold, and prices the whole request from it. A model without `contextTiers`
resolves to the base entry and behaves exactly as before, so this is a no-op for every existing
configuration.

The resolved tier name is emitted as `contextTier` on the ledger trace.

### An omitted tier is a warning, not an error

The module does not refuse a flat-rate entry for a model that has context banding. A workload whose
prompts never approach 272,000 tokens is correctly priced by a flat short-context rate, and forcing
every adopter to configure a band they will never cross would be noise.

Instead the gap is made **observable**: the ledger records which tier priced each request, and the
cost-attribution module ships an `untiered_long_context` alert that fires when requests above
`long_context_review_threshold` (default 200,000 — deliberately *below* the real 272,000 boundary, so
a workload creeping towards it is noticed before it crosses) are being priced at `base`.

The alert threshold defaults to 25 rather than 1 because a genuinely flat-rate model will produce
these legitimately, and an alert that cannot be cleared gets muted — at which point it is worse than
not having one.

### Thresholds are carried in the generator, not inferred

The Retail Prices API publishes the long-context *rates* but never the *threshold*. The meters say
`LongCo` and nothing says how long "long" is.

`CONTEXT_THRESHOLDS` in `scripts/_pricing_meters.py` carries the known values, sourced from the Azure
pricing page, with `--context-threshold ALIAS=TOKENS` as an override. A model with long-context
meters and no known threshold produces **no tier and a warning**, never a guessed boundary.

This follows the same rule as `normalise_to_million`, which returns `None` for an unrecognised unit
rather than assuming 1K: a guess that lands on the wrong side of a boundary prices real traffic
incorrectly and looks exactly like a correct answer. An explicit gap can be fixed; a confident wrong
number is not noticed until someone reconciles against the invoice.

## Consequences

### The generator needed three further fixes to produce GPT-5.5 at all

Implementing the tier exposed that GPT-5.5 was producing **no rates whatsoever**, so the flat-rate
under-reporting was in fact a total absence:

1. `ShortCo`/`LongCo` were not in the qualifier vocabulary, so every meter was rejected as a
   near-miss.
2. GPT-5.5's meters **contain no `gpt`** — they are named `5.5 ShortCo inp Gl 1M Tokens`. A perfectly
   reasonable `--alias chat=gpt-5.5` matched nothing. The matcher now retries without the family
   prefix when the full name matches nothing at all, and reports that it did so. The retry is
   deliberately conditional on a total miss, so `gpt-4o` can never strip to `4o` and start absorbing
   unrelated families.
3. `ShortCo PP` (priority processing) is **2.5× the standard rate** and was eligible for selection.
   It is now excluded alongside batch and provisioned meters, which are likewise different
   commercial paths rather than different prices for the same thing.

### Two new guards in the generator

A tier whose input rate is not *above* the base rate is dropped as implausible, on the same reasoning
as the existing "cached must be cheaper than input" guard: it indicates the bands have been swapped
somewhere in parsing, and emitting them reversed would make long requests look cheap.

A tier is also dropped if no long-context input rate was parsed, rather than emitting a partial card.

### Within a matched tier, rates never inherit from the base entry

If a tier omits its cached rate, it falls back to **that tier's own input rate**, not to the base
entry's cached rate. Inheriting would apply a short-context cached price to a long-context request —
an error in the direction nobody investigates, because the bill comes out lower than expected rather
than higher.

### One fewer parse per request

Resolving the rate card into a variable replaced two separate base64 decodes and JSON parses of the
pricing map per request (one for rates, one for the effective date) with one. The tiering work is
therefore net neutral to slightly positive on inbound latency, not a cost.

## Alternatives considered

**Separate aliases for short and long context, e.g. `chat` and `chat-long`.** Zero module changes,
and some teams do route this way deliberately. Rejected as the default because it pushes a billing
detail into the client contract: every caller would have to know the threshold and pick the right
alias, and a caller that picks wrong is mis-billed rather than merely mis-routed. The accelerator's
position is that the gateway knows the prompt length and should apply the correct rate without being
told.

**Deriving the threshold from the model's advertised context window.** Appealing, and wrong: the
pricing boundary and the context limit are different numbers set for different reasons, and nothing
guarantees they stay related.

**Graduated (marginal) tiers.** Modelled first, then discarded once Azure's worked example was
checked and found to reprice the whole request. Implementing marginal pricing would have
under-reported every long-context request by roughly half while appearing more sophisticated.
