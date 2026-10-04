# ADR-0005: Telemetry sink selection — custom metrics and a trace ledger, not one or the other

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** nothing
- **Related:** ADR-0004 (the chargeback model this telemetry feeds), ADR-0007 (the consumer identity carried on the ledger)

## Context

Three sinks are available to an APIM policy for AI workloads, and they are not interchangeable.

`llm-emit-token-metric` writes prompt, completion and total token counts to **Azure Monitor custom
metrics**. Metrics are cheap, aggregate, and arrive in about a minute, which makes them the only sink
fast enough to alert on a runaway agent loop. They are also hard-limited: a custom metric supports at
most **five dimensions**, **100 unique values per dimension**, and **1 000 active time series per
namespace**. Exceeding any of those does not error, does not warn, and does not appear in a portal
blade. It **silently discards data**. There is no signal, and the metric keeps reporting — just not
the whole truth.

The `<trace>` policy writes a structured record to **Application Insights** as a `traces` row with the
policy's metadata in `customDimensions`. It has no cardinality ceiling, so it can carry a consumer
identity, a correlation id and a dozen token counters per request. Its own trap is different and just
as quiet: `trace` emits to Application Insights **only when the API's diagnostic verbosity is set to
`verbose`**, and what it emits is then subject to the logger's **sampling percentage**. A default APIM
deployment samples. A naive `trace`-based cost tracker therefore reports a fraction of real usage,
looks completely healthy, and the number it produces is internally consistent — which is exactly the
property that stops anyone from checking it.

`log-to-eventhub` streams unsampled records to an Event Hub. It is the only option with no sampling
and no retention ceiling, and it is also an additional resource with an additional bill.

Each sink is a trap for a different reason, and the common failure is to pick one and discover its
particular silence later.

## Decision

Use **both** primary sinks, for the jobs each is actually good at, and ship the Event Hub as an opt-in
third.

### Metrics for speed, with deliberately bounded dimensions

`llm-emit-token-metric` is emitted into the `metric_namespace` (default `aigateway`) with exactly four
dimensions: **Product ID**, **Operation ID**, **ModelAlias** and **Environment**. All four are bounded
by configuration — products, operations, model routes and environment name are all finite sets under
the adopter's control, and all four stay far below 100 distinct values in any realistic deployment.

A consumer dimension is **never** added here, and that is the single most important sentence in this
ADR. Consumer identity is unbounded by nature: subscriptions are created as applications onboard, and
Entra application ids more so. Adding it as a dimension would work in a demo, work in a pilot, and
then cross a limit in production and start discarding data without saying so. The cheapest-looking
change available — one more `<dimension>` line — is the one that would quietly destroy the data the
whole module exists to produce.

This is what makes the metric alert worth having: `token_burn_rate` reads the `Total Tokens` metric at
`PT1M` frequency over a `PT5M` window, because it is the only alert in the module fast enough to catch
spend that happens in minutes. Every other alert is a log query on an hourly cadence.

### The trace ledger for fidelity, with the sampling trap closed in Terraform

The per-request chargeback ledger is an Application Insights `trace` emitted in the outbound section,
carrying the correlation id, environment, product, subscription id, consumer identity and type, API
and operation ids, model alias, deployment, backend model, pool, status code, streaming and
`usageMeasured` flags, the full set of token counters, the estimated cost, the pricing effective date,
the resolved context tier and the request latency.

The sampling trap is closed by configuration rather than by documentation:

- `azurerm_api_management_api_diagnostic` sets `verbosity = "verbose"` **on the LLM API only**, not
  globally, so the ingestion cost of full fidelity is paid where the ledger is produced and nowhere
  else.
- `diagnostic_sampling_percentage` defaults to **100**, and its description says plainly that it
  should be left there when cost attribution is on.
- The estimated cost is written with `ToString("R", InvariantCulture)` — round-trip precision, not a
  rounded string. Rounding per request and then summing millions of requests accumulates material
  error, so the ledger stores full precision and the workbook formats it.

Because configuration can be changed after the fact, the `telemetry_gap` alert compares the ledger's
record count against the APIM `requests` count over a six-hour window and fires when more than 5% is
missing. Its description names the likely cause, which is somebody moving sampling off 100 to reduce
Application Insights ingestion cost. It requires two failing periods out of three before firing, so
ordinary ingestion-latency skew does not page anyone.

### Event Hub as an explicit opt-in

`enable_eventhub_audit` provisions a namespace, a hub and an APIM logger, and adds a `log-to-eventhub`
element that writes the same ledger record as the `trace` above — unsampled, one JSON object per
request — for adopters who need audit-grade records that outlive Application Insights retention. It
duplicates the trace rather than replacing it, so that the dashboards and the audit stream cannot
disagree about what happened.

The flag lives on the **ai-gateway** module rather than cost-attribution, even though the requirement
is a cost-attribution one. A policy cannot reference a logger that does not exist yet, so the logger
must be created in the same dependency graph as the policy that names it — and cost-attribution
already depends on ai-gateway for the gateway's name and identity, which makes the reverse ordering
impossible to express.

The namespace is created
with `local_authentication_enabled = false` and the gateway's managed identity is granted **Azure
Event Hubs Data Sender**, so there is no connection string to rotate or leak. It is off by default
because it is a standing cost for a requirement most adopters do not have.

## Consequences

### Full-fidelity telemetry has a real and ongoing price

Pinning sampling to 100 means every gateway request produces an Application Insights record. At high
request volumes that is a non-trivial ingestion bill, and it is a bill that grows with success. The
accelerator's position is that an undercounted cost ledger is worth less than nothing — it is
confidently wrong — so the fidelity is not optional while cost attribution is enabled. Narrowing
verbose diagnostics to the LLM API is the only mitigation offered, and adopters who want more should
reach for Application Insights' own retention and daily-cap controls rather than for the sampling
percentage.

### The same facts exist in two places and can disagree

A metric aggregate and a ledger sum will not match exactly. They have different ingestion paths,
different latencies and different rounding, and the metric is emitted inbound-adjacent while the
ledger is written outbound. Someone will eventually notice the discrepancy and ask which is right.
The answer is that the ledger is authoritative for accounting and the metric is authoritative for
nothing except speed — but that has to be explained each time, and it is a standing cost of carrying
two sinks.

### Four dimensions is a ceiling, not a budget to spend

Five dimensions are permitted and four are used, which looks like room for one more. It is not: the
binding constraint is the 1 000 time series per namespace, which is the product of the cardinalities
rather than their sum. Adding a fifth bounded dimension with even modest cardinality can push the
product over the limit, at which point data is discarded silently. Anyone adding a dimension should
compute the product first, and should assume the answer is no.

### The ledger depends on usage actually being reported

A streaming response returns no usage block unless the caller sets `stream_options.include_usage`.
The gateway policy adds it when absent, but a request whose usage still cannot be read is recorded
with `usageMeasured = false` rather than being dropped or zero-filled — so the gap is visible in the
ledger instead of silently reducing a consumer's share. The `unmeasured_usage` alert fires per product
on the percentage of successful requests in that state. This is a mitigation, not a fix: those
requests still consumed real tokens that nobody is charged for.

### Verbose diagnostics capture more than token counts

Verbosity controls what the diagnostic pipeline records generally, not just the trace policy. The
module keeps `log_request_and_response_bodies` off by default for exactly this reason — prompts and
completions routinely contain personal or confidential data, and the body bytes setting is the
difference between a telemetry stream that is operational and one that is a data-protection concern.
Turning it on is a decision with a compliance owner, not a debugging convenience.

## Alternatives considered

**`llm-emit-token-metric` alone, with a consumer dimension.** The obvious design, and the one most
teams try first: one policy line, near-real-time, no sampling to worry about and no ingestion bill.
It fails on cardinality, and it fails *invisibly*. If exceeding the limits produced an error, or even
a warning, this would be a reasonable choice with a known ceiling. Because the failure is silent
discard, the design produces a cost report that is quietly missing consumers, which is precisely the
failure ADR-0004 is built to avoid.

**APIM diagnostic logs to Log Analytics alone.** Genuinely useful, and the module configures
diagnostic settings regardless. Rejected as the ledger's basis because the gateway logs record the
HTTP transaction, not the token counts and computed cost that only a policy expression can produce.
The response body would have to be parsed out of the log to recover usage, which is both fragile and
a far worse data-protection position than emitting the counters alone.

**Event Hub as the only sink.** The most correct option on fidelity: unsampled, unlimited cardinality,
retention under the adopter's control. Rejected as the default because it imposes a standing cost and
a consumer pipeline on every adopter including the quickstart, and because an Event Hub answers no
question on its own — something still has to read it, store it and query it. It remains available for
the audit case, where that pipeline is justified.

**Turning verbosity to `verbose` globally rather than per API.** One less resource-specific setting to
reason about. Rejected because it multiplies ingestion cost across every API on a shared APIM instance
to benefit one, and a shared gateway is the common brownfield case.
