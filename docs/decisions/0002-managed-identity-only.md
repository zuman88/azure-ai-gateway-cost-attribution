# ADR-0002: Managed identity only for gateway-to-Foundry access

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** nothing
- **Related:** ADR-0001 (backends and pools that this identity authenticates to), ADR-0007 (the inbound half of the identity story)

## Context

Every call the gateway makes to Foundry has to be authenticated. Foundry accounts accept two forms of
credential: an account key, or a Microsoft Entra bearer token presented by a principal that holds an
appropriate role on the account.

The key is easier to start with and worse at everything afterwards. It is an account-wide secret with
no scope, no expiry and no identity attached. Whoever holds it is indistinguishable from the gateway,
so an audit log records that *something* called the account and nothing more. Using it means the key
has to exist somewhere the gateway can read it, which in practice means an APIM named value, a Key
Vault secret, or — in the worst and most common case — a Terraform variable, which puts it in state.
It then has to be rotated, which means a second copy exists during every rotation, and the rotation
itself is a change with an outage window.

None of that is unique to AI workloads. What *is* specific here is the blast radius. A Foundry account
key is a key to spend money: a leaked key does not merely expose data, it lets an unknown party
consume the organisation's model quota and generate an invoice. The quota exhaustion is noticed before
the leak is.

APIM has a system-assigned managed identity and the `authentication-managed-identity` policy, which
acquires and caches a token for a given resource identifier and writes it to a variable. Foundry
exposes the `Cognitive Services User` role, which grants the data-plane inference permission without
granting management of the account or the ability to read its keys.

## Decision

The gateway authenticates to Foundry **only** with its system-assigned managed identity. No Foundry
key is read, stored, templated or accepted as an input anywhere in the accelerator.

Three pieces make this true rather than aspirational:

1. `azurerm_api_management` is created with `identity { type = "SystemAssigned" }`, and the module
   resolves `apim_principal_id` from that identity — or from the existing instance's identity in the
   brownfield case, where the module is pointed at `existing_api_management_id`.

2. `azurerm_role_assignment.gateway_to_foundry` grants **`Cognitive Services User`** to that principal
   on each account id supplied in `foundry_account_ids`. The role is the smallest one that permits
   inference; it does not confer key access, which means the gateway's own identity could not read a
   key even if one existed. `skip_service_principal_aad_check` is set because the principal is a
   freshly created service principal and the assignment would otherwise race its directory
   replication.

3. The gateway policy calls `authentication-managed-identity` with `ignore-error="false"` for the
   resource given by `backend_auth_resource`, defaulting to `https://cognitiveservices.azure.com`,
   writing the token to `backendToken` for the outbound call. `ignore-error="false"` is deliberate: a
   failed token acquisition should fail the request loudly rather than send an unauthenticated call
   and surface a confusing `401` from Foundry.

Alongside this, the `foundry-models` module sets `local_auth_enabled = false` on the accounts it
creates. That is the part that converts a convention into a property of the system — with local
authentication disabled, key-based access to those accounts is **impossible**, not merely unused, and
a well-meaning engineer cannot restore it in a hurry during an incident without a visible
configuration change.

`foundry_account_ids` accepts an empty map, for organisations where role assignments are owned by a
platform team with separate permissions. That is a supported path, not an escape hatch for keys: the
identity is still the only credential, the grant is simply made elsewhere.

## Consequences

### Role assignment propagation is a real and recurring first-apply failure

Azure RBAC assignments are eventually consistent. A `terraform apply` that creates the APIM instance,
assigns `Cognitive Services User`, and then has traffic hit the gateway within the same few minutes
can see `401` or `403` from Foundry even though the assignment exists in the portal. There is nothing
clever to do about this; it resolves on its own. It is listed here because it is the single most
common "the accelerator is broken" report for a keyless design, and because the correct response —
wait and retry — looks indistinguishable from the incorrect response of adding a key.

### Local testing gets harder, and that cost is paid by developers

A developer cannot exercise the gateway path from a laptop by exporting a key. They need a principal
that holds `Cognitive Services User` on the account and a token minted for it, or they test against
Foundry directly and lose everything the gateway adds. For teams used to a key in a `.env` file this
is a genuine reduction in convenience, and it is the main argument anyone raises against this
decision. The counter-argument is that the convenience is exactly the property that causes the key to
end up somewhere it should not be.

### The identity is one principal, so Foundry-side attribution is coarse

Because all gateway traffic reaches Foundry as the gateway's identity, Foundry's own diagnostics
cannot distinguish one consumer from another. Per-consumer attribution lives entirely in the gateway's
ledger, which is why ADR-0004 and ADR-0005 exist at all. Anyone hoping to reconcile a per-consumer
figure against a Foundry-side log will not find one; they will find one line per gateway.

### Moving the gateway means re-granting

A system-assigned identity is bound to the resource. Recreating the APIM instance produces a new
principal id, which invalidates every existing role assignment, and a deployment that recreates APIM
for an unrelated reason will take an outage against Foundry until the assignments catch up. A
user-assigned identity would survive this. It was not chosen because the identity-per-gateway model is
easier to reason about and because recreating a production APIM instance is already a disruptive event.

### The resource identifier is configurable, and that is not cosmetic

`backend_auth_resource` defaults to `https://cognitiveservices.azure.com` because that is the
resource Foundry's inference surface accepts. It is exposed as a variable rather than hard-coded
because Foundry answers on more than one hostname family and the correct audience can differ with the
API surface in use. Changing it is a one-line change; discovering that it needed changing, from a
`401` with no detail, is not pleasant. It is called out here so the knob is findable.

## Alternatives considered

**Key in Key Vault, referenced by an APIM named value.** The conventional answer, and genuinely much
better than a key in state: the secret is centrally stored, access-controlled and auditable, and
rotation can be automated. It lost because it solves the storage problem and not the existence
problem. The key still exists, still grants full account-level data-plane access to anyone who obtains
it, and still has to be rotated on a schedule that someone owns. A managed identity removes the
object rather than protecting it, and `local_auth_enabled = false` makes the removal enforceable.

**User-assigned managed identity.** Survives APIM recreation, can be pre-created so that role
assignments propagate before the gateway exists — which would mitigate the first consequence above —
and can be shared across several gateways. Rejected as the default because a shared identity makes
"which gateway called this account" unanswerable, and because it adds a resource and a lifecycle for
adopters to manage in exchange for mitigating a one-off timing problem. It is a reasonable change for
an organisation that recreates gateways often.

**Keeping key support as an optional input for brownfield adoption.** Attractive for a module intended
to be dropped into an existing estate. Rejected because an optional key path is a path, and paths get
taken: the variable would be set during a migration, the migration would stall, and the accelerator's
central security claim would quietly become false for that deployment with no signal that it had.
Brownfield adopters instead point the module at existing accounts and grant the role, which is work
measured in minutes.
