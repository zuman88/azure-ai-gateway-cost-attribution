# ADR-0007: Microsoft Entra ID for caller authentication and cost attribution

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** nothing
- **Related:** ADR-0002 (keyless outbound access to Foundry), ADR-0004 (the chargeback model this identity keys), ADR-0005 (why no consumer dimension goes on the token metric)

## Context

The gateway authenticates *outbound* to Foundry with its managed identity, so no model key exists
anywhere in the system. Inbound, however, the only credential was an API Management subscription key
in the `api-key` header.

That asymmetry is easy to miss, because the key does work. The problem is what it is worth as
evidence. A subscription key is a bearer secret with no binding to the workload that holds it. In
practice it gets copied into a second service during an incident, shared between two teams who are
nominally one consumer, pasted into a notebook, or committed. From that moment the chargeback report
attributes spend to the wrong consumer, and nothing anywhere indicates that it has happened. The
report stays internally consistent and quietly wrong, which is the worst property a cost report can
have — it is believed.

This matters more for a model gateway than for a typical API, because the gateway's primary output
*is* an attribution ledger. The cost-attribution module exists to answer "who spent this". Keying
that answer on a secret that can be silently duplicated undermines the module's entire purpose.

A Microsoft Entra token is bound to an application registration or a managed identity. It cannot be
meaningfully copied — a copy still identifies the same application — it expires without anyone
revoking it, and it can be revoked centrally when it must be.

The complication is that APIM derives the **product** from the subscription key. The product is what
carries per-tier token limits and per-tier model entitlement, and APIM does not execute product-scope
policy for a request it cannot associate with a product. So removing the key is not a pure upgrade:
it also removes a governance tier.

## Decision

Introduce a `caller_authentication` variable on the `ai-gateway` module with three modes, defaulting
to the existing behaviour.

### `subscription_key` (default)

Unchanged. Subscription key only, attribution by subscription id. Appropriate for a proof of concept
and for anyone adopting the accelerator incrementally. Chosen as the default so that upgrading the
module never breaks a working deployment.

### `both` (recommended for production)

Both credentials are required. The subscription key continues to select the product — so token
limits, quotas and `allowed_models` all keep working exactly as before — while the Entra token
establishes who the caller actually is. Attribution uses the Entra identity.

This is the recommended mode because it is the only one that strengthens attribution without
surrendering a governance capability. The cost is that callers send two credentials.

### `entra_id`

Entra token only; `subscription_required = false` on the API.

The trade-off is explicit and enforced at plan time rather than discovered in production: with no
subscription there is no product, so product-scope policy never runs. Per-product token limits and
per-product model entitlement do not apply. The module therefore:

- requires `caller_authentication.tokens_per_minute` in this mode and applies it as a single
  gateway-wide `llm-token-limit` keyed on the caller identity, so the gateway is never left with no
  rate ceiling at all; and
- **fails the plan** if any product declares `allowed_models` or `tokens_per_minute`, rather than
  accepting the configuration and ignoring it. A quota an operator believes is enforced, and which is
  not, is worse than no quota — it removes the vigilance that the absence of a quota would prompt.

## Consequences

### Validation is mandatory, not optional

The module refuses a configuration that sets neither `audiences` nor `client_application_ids`.
Validating only the tenant and the signature accepts *any* token that tenant has ever issued,
including one minted for Microsoft Graph or for an unrelated internal API by a user who has no
entitlement to the gateway. The caller never needed access to the gateway to obtain such a token.
That is a confused-deputy vulnerability wearing the costume of authentication, and it is a common
enough misconfiguration that it is worth making unrepresentable rather than merely documenting.

Checking both is better than either: the audience proves the token was minted for this gateway, and
the client application id proves which application asked for it.

### Claim selection is configurable, with a deliberate default

`consumer_claim` defaults to `appid` with fallbacks `["azp", "oid", "sub"]`, tried in order.

`appid` carries the calling application's client id in v1.0 tokens, which is the right identity for
service-to-service traffic — and service-to-service is essentially all a model gateway carries.
v2.0 tokens use `azp` in its place. `oid` and `sub` catch managed identities and user-delegated
calls. An identity that resolves to none of them is recorded as `unattributed` rather than left
blank, so it appears in a report as an anomaly to chase instead of vanishing from it.

### The ledger keeps working across the rollout

The trace emits `consumerId`, `consumerName` and `consumerType` alongside the existing
`subscriptionId`. The cost-attribution KQL prefers `consumerId` and falls back to `subscriptionId`
when it is absent, so queries, alerts and workbooks work unchanged against traces emitted before the
feature was enabled, and during a staged rollout where both shapes are in flight.

`consumerType` is carried deliberately: it lets a reader distinguish a token-backed attribution from
a key-backed one rather than treating both as equally trustworthy.

### Rate limiting is keyed on the subscription, not the token

In `both` mode the product policy's `llm-token-limit` continues to use
`counter-key="@(context.Subscription.Id)"`. It is *not* re-keyed to the Entra identity.

This is deliberate. Product-scope policy runs **before** API-scope policy in APIM's
Global → Product → API → Operation inbound order, so at product scope the token has not yet been
validated. Deriving a counter key from an unvalidated token would let a caller present an arbitrary
`appid` and consume — or exhaust — another identity's rate budget. The subscription id is already
authenticated by the time product policy runs, so it remains the safe counter key.

Attribution keys on the Entra identity; rate limiting keys on the subscription. Separating them is
not an inconsistency, it is the point.

### Token metrics are untouched

No consumer dimension is added to `llm-emit-token-metric`. Azure Monitor custom metrics allow at most
five dimensions, 100 unique values per dimension, and 1000 active time series per namespace, and
exceeding any of those **silently discards data**. Consumer cardinality is unbounded by nature.
Per-consumer attribution stays in the Application Insights ledger, which has no such ceiling.

### Operational cost

Callers must obtain a token. For a workload with a managed identity this is one line and no secret
to store, which is a net reduction in operational burden. For a workload without one it is a new
app registration — real work, and the main reason `both` is opt-in rather than the default.

## Alternatives considered

**JWT validation with `validate-jwt` against a generic OpenID provider.** More flexible, and the
right choice for a non-Entra identity provider. Rejected as the default because
`validate-azure-ad-token` handles Entra key rollover, tenant resolution and the `Bearer` scheme
without configuration, and nearly every caller of a Foundry gateway already has an Entra identity.
`validate-jwt` remains available through a custom policy fragment.

**Client certificates (mTLS).** Strong, and genuinely bound to the caller. Rejected because
certificate lifecycle management is a larger operational commitment than app registrations for most
adopting teams, and because it attributes to a certificate rather than to an identity that already
exists in the directory.

**Keeping subscription keys and solving attribution with a stricter key-issuance process.** Rejected
because it is a process control over a technical problem: it reduces how often the key is copied
without making the copy detectable, and a cost ledger needs the latter.
