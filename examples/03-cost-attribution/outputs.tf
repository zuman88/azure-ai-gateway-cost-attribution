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
  description = "The chargeback workbook. Open it, set the actual spend figure from Cost Management, and read the allocation."
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

output "reconcile_command" {
  description = "Compare the gateway's estimate against what Azure actually billed."
  value       = <<-EOT
    python scripts/reconcile_costs.py \
      --app-insights-id ${var.application_insights_id} \
      --scope ${coalesce(var.foundry_resource_group_id, "<foundry resource group id>")} \
      --days 30
  EOT
}
