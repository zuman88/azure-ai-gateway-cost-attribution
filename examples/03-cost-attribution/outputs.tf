output "openai_base_url" {
  description = "Base URL for OpenAI-compatible clients."
  value       = module.ai_gateway.openai_base_url
}

output "gateway_principal_id" {
  description = "Managed identity that must hold Cognitive Services User on every Foundry account listed in foundry_backends."
  value       = module.ai_gateway.principal_id
}

output "model_aliases" {
  description = "Aliases published by the gateway."
  value       = module.ai_gateway.model_aliases
}

output "subscription_keys" {
  description = "Per-application subscription keys."
  value       = module.ai_gateway.subscription_keys
  sensitive   = true
}

output "chargeback_workbook_id" {
  description = "The chargeback workbook. Reconciliation happens here: open it, enter the actual Foundry spend for the period from Cost Management, and the workbook allocates that real figure across consumers by their share of total tokens."
  value       = module.cost_attribution.workbook_id
}

output "alert_rule_ids" {
  description = "The alerts guarding chargeback accuracy: unpriced models, unmeasured usage, product spend, telemetry gaps and token burn rate."
  value       = module.cost_attribution.alert_rule_ids
}

output "ledger_query" {
  description = "KQL projection of the chargeback ledger. Start here when building reporting outside the workbook."
  value       = module.cost_attribution.ledger_query
}

output "reconciliation_note" {
  description = "How to turn the gateway's estimate into a defensible chargeback number."
  value       = <<-EOT
    The per-request cost in the ledger is an *estimate* from published retail rates. It knows nothing
    about your EA discount, reservations or PTU amortisation, so never bill from it directly.

    Instead, each period:
      1. Read actual Foundry spend for ${coalesce(var.foundry_resource_group_id, "<foundry resource group id>")} from Cost Management.
      2. Enter it in the workbook's ActualCostUSD parameter.
      3. The workbook allocates that figure by each consumer's share of total tokens.

    See docs/cost-attribution.md for why allocation is used instead of summing the estimates.
  EOT
}
