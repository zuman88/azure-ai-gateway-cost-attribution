# ADR-0003: The `azapi` provider for backend pools

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** nothing
- **Related:** ADR-0001 (the pools this resource creates), ADR-0006 (alias routing, which addresses pools by name)

## Context

ADR-0001 commits the accelerator to API Management backend pools. A pool is not a separate ARM
resource type — it is a `Microsoft.ApiManagement/service/backends` resource whose `properties.type` is
`"Pool"` and which carries a `properties.pool.services` array of member backend ids with their
priority and weight.

The AzureRM provider models the backend resource, and models it well for the ordinary case: this
module uses `azurerm_api_management_backend` for every Foundry endpoint and for the Content Safety
endpoint, including the nested `circuit_breaker_rule` block with its `failure_condition` and
`status_code_range`. What the provider's schema does not expose is the pool shape. There is no `type`
argument to set to `Pool` and no block for pool membership, so a pool cannot be expressed through that
resource at all.

This is ordinary provider lag rather than anything unusual. The capability is newer than the schema,
the API version that carries it is a preview version, and provider schemas follow stable API surface.
The question for the accelerator is not whether the gap will close — it almost certainly will — but
what to do in the meantime, given that the pool is the single most valuable thing the module
configures.

The realistic options are: wait for the provider, create pools outside Terraform, or reach the API
directly with `azapi`.

## Decision

Backend pools are created with `azapi_resource`, against
`Microsoft.ApiManagement/service/backends@2024-06-01-preview`, one per model alias, with
`parent_id` set to the APIM instance id and the body supplying `type = "Pool"` and the member
services array built from the route's `backend_priority` and `backend_weight` maps.

Three details of that resource are deliberate:

**Member ids come from the AzureRM resources, not from strings.** Each service entry references
`azurerm_api_management_backend.foundry[backend_key].id`. That is what makes the dependency between
the two providers explicit in the graph rather than implicit in apply ordering, and it means renaming
a backend cannot leave a pool pointing at an id that no longer exists. A `depends_on` on the backend
resource set is kept as well, because pool creation against a member that is still being created
fails in a way that reads as a transient API error rather than as a missing dependency.

**`schema_validation_enabled = false`.** `azapi` validates bodies against its embedded copy of the ARM
schema. For a preview API version that embedded schema can be incomplete or lag the service, and a
false rejection at plan time is worse than no validation, because it blocks a configuration the
service would have accepted. The cost — that a malformed body now fails at apply rather than at plan —
is accepted and discussed below.

**Everything else stays on AzureRM.** `azapi` is used for this resource and nothing more. The module's
other forty-odd resources, including the backends that are pool members, are AzureRM. The provider
requirement in `versions.tf` is pinned to `>= 2.0.0, < 3.0.0` and carries a comment stating why it is
there, so the dependency is justified at the point someone would ask about it.

## Consequences

### An extra provider in every root module that uses the gateway

Adopters inherit `Azure/azapi` whether or not they wanted it. That is a second provider to allow
through any registry mirror or network policy, a second entry in every lock file, and a second
upgrade to consider. For organisations with an approved-provider list it is a real piece of process
work before the accelerator can be used at all, and the effort is out of proportion to the one
resource it supports. This is the main cost of the decision and there is no way to avoid it while
keeping pools.

### The resource is a raw API body, with the properties that implies

`azapi_resource` bodies are not typed. A misspelled property name is not a plan error; it is a field
the service ignores or rejects at apply time. With schema validation disabled, the feedback loop for
getting the pool shape wrong is a failed apply rather than a failed plan, and the error comes back in
ARM's vocabulary rather than Terraform's. Reviewing a diff on this resource also requires knowing the
API shape — a reviewer cannot lean on the schema to tell them whether `priority` belongs where it has
been put.

### Pinning to a preview API version is a standing commitment

`2024-06-01-preview` is pinned rather than floating, which is correct — a floating preview version
would let the service change the resource's behaviour under a configuration that has not changed. The
consequence is that the pin is now something that has to be reviewed periodically rather than
something that maintains itself, and preview API versions are retired. This is the reason the
migration path below matters more than it usually would.

### Drift detection is weaker than for an AzureRM resource

`azapi` compares the body it sent with what it reads back. Properties the service defaults, normalises
or returns in a different form can produce either a persistent spurious diff or, in the other
direction, silence about a change made in the portal. The accelerator's position is that pools are
managed exclusively through Terraform and portal edits to them are drift to be corrected rather than
state to be preserved, but it is worth saying that this resource is less self-correcting than the
ones around it.

### Two providers now share one logical resource type

Backends exist in two provider namespaces in the same module: `azurerm_api_management_backend` for
endpoints, `azapi_resource` for pools. Anyone searching the codebase for "backend" finds both, and
anyone importing existing infrastructure has to know which tool applies to which object. The comment
blocks in `main.tf` and `versions.tf` exist to shorten that confusion, not to remove it.

## Migration path

When AzureRM exposes pools on `azurerm_api_management_backend` — a `type` argument and a pool
membership block, on a stable API version — the move is mechanical and does not require recreating
anything:

1. Add the equivalent `azurerm_api_management_backend` resources for the pools, with `count = 0` or
   behind a feature flag, and confirm the schema covers priority and weight.
2. `terraform state rm` each `azapi_resource.model_pool` entry and `terraform import` the same ARM
   resource ids into the new AzureRM addresses. Pool resource names are deterministic
   (`pool-<alias>`), so the ids are derivable from `var.model_routes` without inspecting the cloud.
3. Confirm a plan is empty, then delete the `azapi` resource, its `depends_on`, and the provider
   requirement from `versions.tf`.

Step 2 is the one that must be done carefully: removing from state and importing is non-destructive,
whereas simply deleting the `azapi` resource and adding the AzureRM one would destroy and recreate
every pool, and a pool that does not exist is a `set-backend-service` to a backend id that does not
resolve — that is a full outage for every alias, not a degraded one.

## Alternatives considered

**Wait for AzureRM and ship the retry-loop pattern in the meantime.** Keeps the provider set minimal
and avoids every consequence listed above. Rejected because it gives up the decision in ADR-0001 for a
schedule nobody controls: the accelerator would ship with its most-cited improvement absent, and the
eventual switch would be a policy rewrite rather than a state move. Trading one resource's ergonomics
for the gateway's resiliency model is not a good trade.

**ARM template or Bicep deployment nested inside Terraform** (`azurerm_resource_group_template_deployment`).
Uses only the AzureRM provider, which answers the approved-provider objection. Rejected because it is
strictly worse on every other axis: the template's contents are opaque to the plan, the member backend
ids have to be threaded through as parameters, and deleting the deployment resource does not reliably
delete what it created — so the lifecycle the module appears to manage and the lifecycle it actually
manages diverge.

**Creating pools once by hand, out of band.** Fastest to the first working demo. Rejected outright: an
accelerator whose golden path requires portal clicks is not reproducible, and the pool membership is
precisely the thing that changes when a region or a deployment is added.
