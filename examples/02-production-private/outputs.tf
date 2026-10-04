output "openai_base_url" {
  description = "Base URL for OpenAI-compatible clients."
  value       = module.ai_gateway.openai_base_url
}

output "model_aliases" {
  description = "Aliases callers may put in the model field."
  value       = module.ai_gateway.model_aliases
}

output "gateway_principal_id" {
  description = "The gateway's managed identity. This is the only thing with data-plane access to Foundry."
  value       = module.ai_gateway.principal_id
}

output "backend_pool_ids" {
  description = "Backend pool per alias. Useful when checking circuit breaker state in the portal."
  value       = module.ai_gateway.backend_pool_ids
}

output "subscription_keys" {
  description = "Per-application subscription keys."
  value       = module.ai_gateway.subscription_keys
  sensitive   = true
}

output "chargeback_workbook_id" {
  description = "The chargeback workbook."
  value       = module.cost_attribution.workbook_id
}

output "budget_id" {
  description = "Cost Management budget watching actual Foundry spend."
  value       = module.cost_attribution.budget_id
}

output "nat_gateway_public_ip" {
  description = "Stable outbound address, when the NAT gateway is enabled. Give this to anyone who needs to allow-list your egress."
  value       = module.network.nat_gateway_public_ip
}

output "private_dns_zone_ids" {
  description = "The three Foundry private DNS zones in use."
  value       = module.network.private_dns_zone_ids
}
