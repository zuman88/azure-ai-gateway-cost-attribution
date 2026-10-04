# Example 01 — Quickstart

The smallest deployment that is still worth putting in front of a team: one Foundry account, two models, a public gateway, and two product tiers with real token ceilings.

Pick this one to prove the pattern, run a workshop, or give an application team something to code against on day one. It is not the production topology — [example 02](../02-production-private/) is — but it is the same module code, so moving between them is a change of variables rather than a rewrite. It does not include chargeback; [example 03](../03-cost-attribution/) adds that.

## What this deploys

A resource group containing:

| Module | What it creates here |
|---|---|
| [`foundry-models`](../../modules/foundry-models/) | One Foundry account in `location`, with `gpt-4.1-mini` and `text-embedding-3-small` deployed at 50 capacity each on `GlobalStandard`. |
| [`observability`](../../modules/observability/) | Log Analytics workspace (30-day retention) and Application Insights. |
| [`ai-gateway`](../../modules/ai-gateway/) | API Management on `StandardV2_1`, the LLM API, backend pools, two products, one subscription, and the gateway policy. |

Two things are worth noticing in `main.tf`, because they are the shape of the whole accelerator rather than quirks of this example:

- **The account is keyless.** `local_auth_enabled` defaults to `false` on the Foundry account, so there is no API key to leak. The gateway's managed identity is the only way in, and the role assignment that grants it is made by the gateway module rather than the Foundry module — doing it the other way round would need the gateway's principal id before the gateway exists.
- **Callers code against aliases, not models.** This example publishes `chat-small` and `embed-small`. Which deployment sits behind each is a Terraform variable, so the model underneath can change without touching a single consumer.

The two products are the token ceilings:

| Product | Tokens/minute | Monthly quota |
|---|---|---|
| `experimentation` | 20,000 | 5,000,000 |
| `production` | 100,000 | 100,000,000 |

## Prerequisites

- Terraform **>= 1.9.0**, the `hashicorp/azurerm` provider **>= 5.0.0, < 6.0.0**, and `Azure/azapi` **>= 2.0.0, < 3.0.0**. The azapi provider is not optional: backend pools are created through the Azure API directly because the azurerm provider does not model them yet.
- An Azure subscription, and credentials that can create resource groups, API Management, Cognitive Services accounts and Log Analytics.
- **`Microsoft.Authorization/roleAssignments/write`** on the resource group — the gateway module grants its own managed identity `Cognitive Services User` on the Foundry account. Owner or User Access Administrator will do; Contributor alone will not.
- Model availability is regional. `eastus` is the default because it carries both models at the time of writing; if you change `location`, check the models exist there first.

## Usage

```bash
cd examples/01-quickstart
cp terraform.tfvars.example terraform.tfvars   # then edit it
terraform init
terraform plan
terraform apply
```

The first apply is dominated by API Management provisioning. The v2 SKUs are considerably faster to create than the classic tiers, but it is still the long pole — expect to wait, and expect that wait to be Azure provisioning rather than Terraform spinning.

Then smoke-test it:

```bash
terraform output -raw smoke_test_command    # prints the command below, filled in
python ../../scripts/smoke_test.py \
  --base-url "$(terraform output -raw openai_base_url)" \
  --api-key "$(terraform output -raw demo_subscription_key)" \
  --model chat-small
```

[`scripts/smoke_test.py`](../../scripts/smoke_test.py) uses only the Python standard library, so there is nothing to install. It checks routing, token governance headers, streaming and the error contract.

## Inputs you must set

Only one variable has no default:

| Name | Type | Why |
|---|---|---|
| `publisher_email` | `string` | Publisher email for API Management. Azure sends service notifications here, so use a monitored address. |

Everything else is optional, but two are worth setting deliberately:

| Name | Type | Default | Notes |
|---|---|---|---|
| `name_prefix` | `string` | `"aigw"` | Prefixed to every resource name. It has to win a **globally unique** Foundry custom subdomain, so change it if `apply` fails on a name conflict. |
| `location` | `string` | `"eastus"` | Must be a region that actually carries both models. |
| `environment_name` | `string` | `"dev"` | Used in names, tags and telemetry. |
| `publisher_name` | `string` | `"AI Platform Team"` | Shown in the developer portal. |
| `log_analytics_daily_quota_gb` | `number` | `5` | A cost guardrail, not a retention policy — data beyond the cap is **dropped, not deferred**. |
| `tags` | `map(string)` | `{}` | Merged into the tags every resource gets. |

## Outputs

| Name | Description |
|---|---|
| `openai_base_url` | Point any OpenAI-compatible client at this. |
| `model_aliases` | Aliases callers may put in the `model` field. |
| `demo_subscription_key` | Subscription key for the demo application, passed in the `api-key` header. Marked `sensitive`, so read it with `terraform output -raw`. |
| `smoke_test_command` | The smoke-test invocation, with the URL and key filled in. |
| `application_insights_id` | Where the gateway's telemetry lands. |
| `foundry_accounts` | Foundry accounts behind the gateway. |

## Cost and cleanup

The gateway dominates the bill. **API Management `StandardV2_1` is billed hourly whether or not anyone calls it**, and it keeps billing while it sits idle overnight — this is the line item that surprises people, not the tokens. Log Analytics ingestion is second, capped here at 5 GB/day. The Foundry deployments are `GlobalStandard`, so they are billed per token consumed rather than reserved, and cost nothing when idle.

Destroy it when you are finished:

```bash
terraform destroy
```

## Notes

- **Cost attribution is off here** (`enable_cost_attribution = false`), so no pricing map is needed and no chargeback ledger is written. Turn it on and you also have to supply rates — see [example 03](../03-cost-attribution/).
- The gateway is **public**. There is no VNet, no private endpoint and no Entra ID caller authentication in this example; access is controlled by subscription key alone. That is a deliberate trade for a day-one environment and is not what you should put customer traffic through. [Example 02](../02-production-private/) is the private topology.
- The single subscription, `demo-app`, is attached to the `experimentation` product, so it inherits the 20,000 tokens/minute ceiling. A 429 during a demo is usually this and not a Foundry throttle.
- Background reading: [architecture](../../docs/architecture.md), [policy reference](../../docs/policies.md), [operations](../../docs/operations.md).
