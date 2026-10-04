# ADR-0006: Alias routing on GA primitives, not the preview unified model API

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** nothing
- **Related:** ADR-0001 (the pools an alias resolves to), ADR-0003 (how those pools are created)

## Context

Consumers need a stable address for a model. The naive gateway exposes the physical deployment
directly: the client sends the Foundry deployment name, the gateway forwards it, and the contract
between the two is the deployment's name. That contract is stable right up until the first time
anything interesting happens — a model version upgrade, a region migration, a move from Standard to
provisioned throughput, a rename during an account consolidation — at which point every consumer has
to change code, and they all have to change it at the same time. In practice the deployment is never
renamed, the organisation accumulates deployments named after model versions it stopped wanting two
years ago, and the gateway's governance value is undermined by a naming decision.

API Management does have a native feature for this: a unified model API that fronts multiple model
providers and translates request and response formats automatically. It is the right long-term shape
for a multi-provider gateway. At the time of writing it is **in preview**, which for a reusable
accelerator is disqualifying on its own terms: preview surface changes without notice, is not covered
by the same support commitments, and would put every adopter's production routing path on a contract
that can move.

The question is therefore not whether the unified API is better. It is what to build on GA primitives
that solves the same problem and does not have to be thrown away when the preview lands.

## Decision

The gateway publishes **logical model aliases**. A client sends an alias — `gpt-chat`,
`gpt-reasoning`, `text-embedding` — in the standard `model` field of an OpenAI-shaped request body,
and the gateway resolves it to a backend pool and a physical deployment name.

The alias map lives in `var.model_routes` and is rendered into an APIM **named value** as base64 JSON
by `local.routes_payload`, one entry per alias carrying `pool`, `deployment` and `description`.
Base64 rather than raw JSON is not aesthetic: a named value is substituted verbatim into the
surrounding C# string literal in a policy expression, and raw JSON would terminate that literal on its
first double quote, so the policy would fail to compile the moment a real catalogue was loaded.

In the inbound policy the resolution is two `set-variable` expressions — `targetPool` and
`targetDeployment` — followed by a single `set-backend-service` naming the pool and a rewrite of the
body's `model` field to the physical deployment name. The Azure OpenAI v1 API takes the deployment
name in the `model` field, so there is no per-deployment path rewriting and no `api-version` to track,
which removes a whole category of version-skew bugs. The API is published at `openai/v1` by default,
so an OpenAI SDK needs only its `base_url` changed.

The route table carries **only** `pool` and `deployment`. Priorities and weights are the pool's
business, not the policy's — that separation is the entire point of ADR-0001, and leaking a priority
into the route table would quietly put failover logic back into policy code.

Three behaviours fall out of the design and are worth stating because each one is a deliberate choice:

**An unknown alias returns `400` with the catalogue.** Rather than a bare rejection, the policy
enumerates the published aliases into an `availableModels` array alongside a `correlationId`. A
developer who mistypes an alias gets the answer in the error rather than in a support ticket.

**A missing `model` field returns `400 missing_model`** instead of being forwarded for Foundry to
reject, so the failure is attributed to the gateway's contract rather than appearing as a backend
error.

**`GET /models` is answered by the gateway itself**, from the same route table, with no backend call.
The catalogue a client discovers is therefore the catalogue the gateway will actually honour, which is
not true of a passthrough that proxies the backend's own model list.

Parity is validated at plan time. Because the alias resolves to a pool whose members are treated as
interchangeable, every backend in a route's `backend_priority` must host that route's `deployment`;
`local.parity_violations` fails the plan with the offending alias, deployment, backend and the
deployments that backend does advertise, unless `enforce_deployment_parity` is turned off.

## Consequences

### A model upgrade becomes a data change

Retiring `gpt-4o` for a successor is: deploy the new model to every account in the pool, change one
`deployment` value in `model_routes`, apply. The named value is rewritten, the policy document is
untouched, and no consumer changes anything. The same mechanism covers region migration and a move
to provisioned capacity. This is the capability the decision exists to buy, and it is the reason
`model_routes` is the variable adopters will edit most often.

### The alias map is now a thing somebody owns

Decoupling is not free: there is a mapping, it has to be correct, and it is one more artefact between
a client's request and the model that serves it. When a call behaves unexpectedly, the first question
becomes "what is this alias actually pointing at today", and the answer lives in a named value rather
than in the client's code. The ledger records both `modelAlias` and the resolved `deployment` for
exactly this reason, so a trace can answer the question without anyone opening the portal — but the
indirection itself remains, and it is a real cost for small deployments with one model and one region.

### Aliases need a naming discipline the accelerator cannot enforce

An alias named `gpt-4o` is an alias that will eventually point at something that is not GPT-4o, which
is worse than no alias at all. The examples use capability-shaped names — `gpt-chat`,
`gpt-reasoning` — precisely to avoid baking a model version into a stable contract. Nothing in the
module prevents an adopter from naming an alias after a model version, and plenty will.

### Parity validation will reject configurations that an operator believes are fine

The parity check is strict: it fails on any member of a pool that does not advertise the deployment.
In a brownfield estate where the same model is deployed under different names in different accounts,
this blocks a plan that the operator knows would mostly work. "Mostly" is the problem — the failure it
prevents is a `404` from exactly one pool member, appearing only when load balancing happens to pick
it, which is an intermittent production error with no reproduction steps. The escape hatch exists
(`enforce_deployment_parity = false`) and its error message says in plain words what accepting it
means. Turning it off should be a decision someone writes down.

### Format translation is not provided

Alias routing moves requests; it does not transform them. A client has to speak the OpenAI-compatible
shape the gateway publishes. The route schema carries an `apiFormat` concept in the architecture
documentation to distinguish OpenAI-shaped from Foundry Models-shaped backends, but the gateway does
not rewrite between provider dialects — that is precisely the capability the unified model API adds,
and the honest statement is that this design does not have it. An organisation that must front a
non-OpenAI-shaped provider today needs a separate API and policy, not another alias.

### Resolution costs two JSON parses in the inbound path

`targetPool` and `targetDeployment` each decode and parse the route named value. It is small — the
route table is a handful of entries — but it is work done per request, and a very large catalogue
would make it measurable. Consolidating into a single parse is an obvious optimisation if it ever
matters; it has not been done because the current cost is not detectable next to a model call.

## Migration path

If and when the unified model API reaches general availability, the alias contract is the thing that
makes migration tractable rather than the thing that blocks it. Consumers already send a logical name
in the `model` field, which is the same contract the unified API expects, so the client-facing side
does not change at all. The move would replace the policy's alias-to-pool resolution with the native
feature's own routing configuration, and the parity requirement would be re-evaluated against whatever
backend binding the feature uses.

The migration is therefore a gateway-internal change with no client coordination — which is the test
a decoupling layer should be judged by, and the reason this ADR treats the preview feature as a
future implementation detail rather than as a competing architecture.

## Alternatives considered

**The preview unified model API.** Better on capability in every respect that matters: native
multi-provider support, format translation the policy does not have to implement, and routing
maintained by the platform team rather than by whoever inherits the repository. It lost on readiness,
not on merit. A reusable accelerator's production routing path cannot sit on surface that changes
without notice, and the migration path above is cheap enough that waiting costs little.

**A single passthrough API with no alias layer.** The simplest thing that works: forward the
deployment name the client sent, let Foundry resolve it. Genuinely the right answer for a single-team,
single-region deployment, and it is less code and less indirection. Rejected because it makes the
physical deployment name part of the public contract, which turns every model upgrade, region move
and capacity change into a coordinated multi-team client change — the specific failure that pushed
organisations towards a centralized gateway in the first place.

**One API or one operation per model.** Gives each model its own APIM surface, its own policy and its
own per-model entitlement without needing a product tier. Rejected because it breaks SDK
compatibility — an OpenAI client sends the model in the body, not the path — and because the number
of APIM resources then grows with the model catalogue, so adding a model becomes a structural change
instead of a data change.

**Routing with a `choose`/`when` ladder over alias names in policy.** No named value, everything
visible in one document. Rejected because adding a model becomes a policy deployment, the ladder is
evaluated per request, and policy documents are the hardest part of a gateway to review safely. The
named value makes the catalogue data, which is what it is.
