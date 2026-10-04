# ADR-0001: Native backend pools instead of retry-driven backend selection

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** nothing
- **Related:** ADR-0003 (the pool resource is created through `azapi`), ADR-0006 (alias routing, which is what a pool is addressed by)

## Context

A gateway in front of Foundry has to survive the thing Foundry does most reliably under load: say no.
A Standard deployment that has exhausted its tokens-per-minute allowance returns `429` with a
`Retry-After` header, and that header is not always measured in seconds — a throttled deployment can
ask the caller to come back in minutes or hours. A regional incident produces `5xx` instead. Either
way the gateway's job is to stop sending traffic to that endpoint and start sending it somewhere else.

The obvious way to build this in API Management is a `<retry>` loop in policy: hold an ordered list of
backend URLs in a named value, keep an index variable, call `set-backend-service` with the current
entry, and on a failing status code increment the index and go round again. It works, it is written in
one file, and it is the pattern most hand-built AI gateways use. The implementation this accelerator
replaces used exactly that, in roughly sixty lines of policy expression.

The flaw is that the loop is **stateless per request**. Nothing it learns survives the response. If a
deployment is dead, request number one discovers it, pays the full round trip and the backend's own
timeout, then fails over. So does request number two. So does every request for as long as the outage
lasts. At a few requests per second the gateway spends a meaningful fraction of its capacity, and all
of its added latency, re-establishing a fact it already knew. Worse, each of those probe calls still
consumes the failing deployment's quota, which is precisely the resource that needs to recover.

API Management has first-class primitives for this. A backend can carry a **circuit breaker rule**,
and a backend of `type = "Pool"` holds a set of member backends with priority groups and weights.
Both are platform state, maintained across requests, visible in the portal, and evaluated before any
policy expression runs.

## Decision

Routing targets a **pool**, and resiliency is the pool's responsibility.

`modules/ai-gateway` creates one `azurerm_api_management_backend` per Foundry endpoint, each carrying a
`circuit_breaker_rule`, and one pool per model alias (`pool-<alias>`) whose members are the backends
named in that alias's `backend_priority` map. The gateway policy's entire contribution to failover is a
single `set-backend-service` naming the pool. There is no retry loop, no index variable and no
per-attempt JSON parsing in the hot path.

The platform behaviours the module leans on are these:

- **Priority groups are strictly ordered.** A lower-priority group is reached only when every backend
  in every higher-priority group has a tripped breaker. That is exactly the semantics needed for
  "reserved PTU capacity first, spill to pay-as-you-go, then cross-region" — which is why
  `backend_priority` is a map of backend key to group number rather than a flat list.
- **Weights spread load inside a group.** `backend_weight` is optional and defaults to 1 per member,
  so an unweighted pool round-robins. A weighted pool is also the mechanism for a gradual blue/green
  shift between deployments, with no policy change at all.
- **A breaker can accept `Retry-After` as its trip duration.** `circuit_breaker.accept_retry_after`
  defaults to on. This matters more for Foundry than for an ordinary HTTP backend, because Foundry's
  `Retry-After` is an authoritative statement about when quota will exist again, and guessing a
  shorter trip duration simply re-trips the breaker.
- **A pool holds at most 30 backends**, and APIM supports **one circuit breaker rule per backend**.
  The module validates the first (`backend_priority` is capped at 30 entries per route) and works
  within the second by encoding both throttling and server errors into a single rule using a status
  code range, defaulting to the `429`–`599` span rather than two separate rules it cannot create.

Because a pool treats its members as interchangeable, **deployment-name parity across a pool is a hard
requirement**: every backend in a route's pool must host the deployment that route points at. The
module computes `parity_violations` at plan time and fails the plan through a `terraform_data`
precondition unless `enforce_deployment_parity` is explicitly set to `false`.

## Consequences

### Failover becomes invisible, which is mostly good and occasionally confusing

Once the pool owns backend selection, the gateway policy no longer knows which member served a
request, and cannot. The ledger trace records `pool` and the resolved `deployment`, not the physical
endpoint that answered. Operators who want per-endpoint attribution have to read it from APIM's own
backend diagnostics rather than from the chargeback ledger. This is a real loss of detail in exchange
for a much smaller policy, and it is the trade the accelerator takes deliberately.

### Parity is now a constraint on how models are deployed

Parity is not free. It means an alias cannot be served by a pool whose members host that model under
different deployment names, which in a brownfield estate they very often do. The `foundry-models`
module sidesteps this by deploying an identical deployment set to every account by default, but a team
pointing the gateway at pre-existing accounts may have to rename deployments or split an alias across
two routes. The alternative — per-request backend selection based on which account has the model — is
the retry loop again, with all of its problems.

The failure mode if parity is violated is the reason the check exists: a `404` from exactly one member
of a pool, appearing only when load balancing happens to pick it. That is an intermittent production
error with no reproduction steps, and it is far cheaper to catch in a plan.

### Tuning the breaker is a genuine operational decision, not a default to accept

A breaker that trips too eagerly removes healthy capacity during a brief spike; one that trips too
slowly leaves callers absorbing failures. The module exposes `failure_count`, `interval`,
`trip_duration` and the status code range rather than hard-coding them, which means an adopter who
never looks at them is running on a guess. Honest answer: the defaults are a reasonable starting
point and should be revisited after the first incident, not before.

### One rule per backend forces throttling and errors to share a trip duration

Ideally a `429` with a long `Retry-After` and a transient `503` would be treated differently — the
first is a quota statement, the second is usually momentary. APIM's one-rule-per-backend limit means
both share one rule and one configured `trip_duration`. `accept_retry_after` mitigates it for the
`429` case, because Foundry supplies the duration, but a `5xx` without a `Retry-After` falls back to
the configured value. If APIM later supports multiple rules per backend, splitting them is a small and
obvious change.

### The gateway can still exhaust a pool

Nothing here manufactures capacity. When every member of every priority group has a tripped breaker,
the request fails, and it fails faster than it would have with a retry loop — which is the correct
behaviour but will look like a regression to anyone who measured the old gateway's success rate while
it was quietly spending seconds per request retrying. Breaker trips should be alerted on, which is
why they appear in the operational model rather than being left to the portal.

## Alternatives considered

**The `<retry>` loop over a backend map.** The honest case for it is strong: it needs no preview API
surface, it is entirely expressed in a policy document that can be read top to bottom, and it works
identically on every APIM tier. It lost on statefulness. Every property that makes it simple — no
platform state, everything in the request — is the same property that makes it re-discover failures
on every request and consume the failing backend's quota while doing so. Weighted distribution and
gradual traffic shifting would also have had to be written by hand on top of it.

**Priority groups implemented as multiple pools selected in policy.** Keeps pools for weighting while
letting policy decide when to fall through to the next tier. Rejected because the fall-through
decision is exactly the stateful part, so this recreates the retry loop's weakness while adding the
pool's dependencies.

**Front Door or Traffic Manager in front of multiple regional gateways.** The right answer for
gateway-level availability, and compatible with this decision rather than an alternative to it. It
does not solve backend selection: traffic that reaches a healthy gateway still has to be routed to a
Foundry deployment that has quota left, which is what the pool does.
