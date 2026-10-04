# ADR-0004: Ratio allocation of actual spend, not gateway-estimated dollars

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** nothing
- **Related:** ADR-0005 (the telemetry the ledger is built from), ADR-0007 (which identity a row is charged to), ADR-0008 (keeping the estimate itself honest)

## Context

Azure bills Foundry at the granularity of **resource × meter × region**. The question an organisation
actually wants answered is at the granularity of **application × model × time**. Nothing in the Azure
billing pipeline knows that the claims-triage app exists, and nothing in the gateway knows what the
invoice says. The gateway is the only point in the system where both facts are simultaneously
available — it sees who called and how many tokens of which class they consumed — so it has to be the
point of record for *allocation*, while Azure remains the point of record for *amount*.

The tempting shortcut is to skip the second half. The gateway already computes an estimated cost per
request from a rate card: sum it per consumer, put a currency symbol on it, and publish. It is one
query, it needs no inputs, and it produces a number that looks exactly like a bill.

It is also wrong, and wrong in a direction that is hard to detect. A gateway estimate is computed from
**retail list prices**. The amount the organisation is actually charged differs from list for reasons
that are all entirely legitimate and none of which the gateway can see: Enterprise Agreement or MCA
discounting, reservations, provisioned-throughput amortisation where a PTU deployment's cost has no
per-token relationship at all, commitment tiers, and mid-month price changes. Every one of those
drives a gap between the estimate and the invoice, and the gap is not a constant factor that can be
divided out — PTU amortisation in particular breaks the per-token model entirely, because the capacity
was paid for whether or not anyone used it.

The result is a report that sums to a total nobody can find on an invoice. The first time finance
checks, the model loses its credibility, and it loses it over an error that was structural rather than
sloppy.

## Decision

Publish **both** numbers and treat them as answering different questions.

The gateway's per-request estimate is a **management signal**: it drives alerts, anomaly detection,
budget burn-down and the "is this workload getting more expensive" conversation, where latency matters
and precision does not. It is never presented as an invoice.

Chargeback is computed by **ratio allocation**. The ledger supplies each consumer's measured share of
consumption; the real spend figure for the period supplies the amount; the charge is the product of
the two. If the gateway observed that App A consumed 30% of the tokens, App A is charged 30% of what
Foundry actually cost, whatever that turned out to be.

This is the property that matters: **ratio allocation is invariant to everything that makes the
estimate drift.** A discount, a reservation, a commitment tier or a mid-month repricing changes the
numerator and the denominator together and leaves every consumer's share untouched. The allocation
does not need to know the discount exists.

In the module this appears as:

- `local.ledger_query` in `modules/cost-attribution`, the single KQL projection every alert and
  workbook query starts from, so the ledger schema is defined once and the reporting cannot drift
  away from it.
- The workbook's allocation table, which computes `Share of tokens` per consumer and product and then
  derives `Allocated USD` from the actual spend figure for the period. When no actual figure has been
  supplied it falls back to showing the gateway's own estimate, so the workbook is useful before
  anyone has done the reconciliation work.
- `Estimated USD` and `Allocated USD` sitting **side by side in the same table**. That adjacency is
  the point, not a layout choice: the gap between the two columns is the signal that the pricing map
  has gone stale, and hiding either column would remove the only place that gap is visible.
- An `azurerm_consumption_budget_resource_group` over the Foundry resource group, which watches what
  Azure actually charges rather than what the gateway estimated, with actual and forecast thresholds
  separated — anything above 100% of budget can only be a forecast, so the module routes those
  thresholds to `Forecasted` notifications automatically.

## Consequences

### Chargeback is periodic and retrospective, by construction

The authoritative amount only exists after Azure has billed, so an allocated figure cannot be
real-time. Per-consumer chargeback lands on a billing cadence, and anyone who wants a number *today*
is looking at the estimate, with all of its caveats. That is a genuine capability gap compared to
publishing estimates alone, and it is the main thing adopters find frustrating. The estimate exists
precisely to fill it — for alerting and anomaly detection, where being approximately right within
minutes beats being exactly right next month.

### Someone has to supply the actual figure

The accelerator does not silently reach into billing data on the adopter's behalf. The workbook takes
the real spend figure for the period as an input and does the allocation arithmetic from it. That
makes the reconciliation step explicit and auditable, and it means the number in the report is one a
human sourced from Cost Management rather than one the tool asserted — but it is also a recurring
manual step, and a step that is skipped is a report running on estimates while looking like it is
running on actuals. The fallback behaviour is deliberately visible for this reason: the estimate
column and the allocated column are both on screen, so a reader can see which one is doing the work.

### Allocation is only as good as the ledger's coverage

Ratio allocation redistributes the **entire** invoice across the consumers the ledger observed. A
consumer whose requests were not recorded does not get a smaller share — it gets no share, and its
spend is silently redistributed across everybody else, who are then over-charged. Missing telemetry
is therefore not a reporting inconvenience, it is a correctness failure that propagates into other
consumers' bills. This is why the module ships the `unmeasured_usage` alert for streaming callers that
return no token counts, and the `telemetry_gap` alert that compares ledger record count against the
gateway's own request count. ADR-0005 covers why those gaps happen at all.

It is also why the workbook's data-quality section is placed where it cannot be missed: if unpriced or
unmeasured requests are non-zero, every allocation above it is distorted.

### Tokens are a proxy for cost, and an imperfect one

The allocation key is share of tokens. Tokens are not uniformly priced — an output token costs several
times an input token, a cached input token costs a fraction of an uncached one, and a long-context
request may be priced at a different rate card entirely. A consumer whose traffic is output-heavy is
therefore under-charged relative to one with the same token count but a prompt-heavy profile, and the
distortion grows as consumer mixes diverge. Allocating per meter rather than per total token would
remove this, at the cost of needing the invoice broken down by meter and a per-meter join on the
ledger. The ledger carries what such a refinement would need — `promptTokens`, `billablePromptTokens`,
`cachedTokens`, `cacheWriteTokens`, `completionTokens` and `meterName` on the rate card — so the
refinement is available to anyone who needs it, but the shipped default is the simpler key, and
that simplification is a real inaccuracy rather than a rounding detail.

### Bypassing the gateway is unattributable, and now also distorting

Any traffic that reaches a Foundry account without passing through the gateway appears in the invoice
and not in the ledger, so it is allocated across the gateway's consumers in proportion to their
shares. The network posture in the production example, which disables public access on the Foundry
accounts, is what keeps this from happening — it is load-bearing for the cost model, not only for
security.

### The estimate still has to be maintained

Ratio allocation is immune to pricing drift, but the variance signal that tells anyone the pricing map
is stale is computed *from* the estimate. Letting the estimate rot therefore does not corrupt the
chargeback numbers — it blinds the mechanism that was supposed to tell you something was wrong. The
rates are generated from the Retail Prices API rather than hand-typed for this reason, and the
variance between the two columns is the thing to watch.

## Alternatives considered

**Publish gateway-estimated dollars as the chargeback figure.** Simple, immediate, needs no billing
access, and for an organisation paying list price with no reservations it is close to correct. It
lost on defensibility: the first time the sum of the report does not match the invoice, the report is
discredited, and the mismatch is guaranteed for anyone with a discount. A number that cannot survive
being checked is not a chargeback number.

**Allocate from Foundry-side telemetry rather than the gateway's.** Appealing because it would be
closer to the billed resource. Rejected because of ADR-0002: all gateway traffic reaches Foundry as a
single managed identity, so Foundry-side records cannot distinguish consumers. The information simply
is not there.

**One Foundry account per consumer, so Azure's own billing does the attribution.** The only approach
that needs no ledger at all, and it is genuinely correct — Cost Management would answer the question
directly, per resource. Rejected because it reintroduces exactly the sprawl the gateway exists to
remove: per-team accounts, per-team quota that cannot be pooled, per-team deployments to keep at
parity, and a quota fragmentation problem that gets worse with every new consumer. It also scales
badly against subscription-level resource limits. Shared capacity with measured allocation is the
trade this accelerator makes, and the allocation ledger is the price of the sharing.
