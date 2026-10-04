output "api_management_id" {
  description = "Resource ID of the API Management instance fronting the gateway."
  value       = local.apim_id
}

output "api_management_name" {
  description = "Name of the API Management instance."
  value       = local.apim_name
}

output "gateway_url" {
  description = "Base gateway URL, for example https://contoso-apim.azure-api.net."
  value       = local.apim_gateway_url
}

output "openai_base_url" {
  description = <<-EOT
    Drop-in base_url for an OpenAI client. Point any OpenAI-compatible SDK at this value, pass the
    product subscription key in the api-key header, and send a gateway model alias in the model field.
  EOT
  value       = "${local.apim_gateway_url}/${var.api_path}"
}

output "principal_id" {
  description = "Object ID of the gateway's system-assigned managed identity. Grant it Cognitive Services User on any Foundry account it must reach."
  value       = local.apim_principal_id
}

output "api_name" {
  description = "Name of the published LLM API."
  value       = azurerm_api_management_api.llm.name
}

output "api_id" {
  description = "Resource ID of the published LLM API."
  value       = azurerm_api_management_api.llm.id
}

output "model_aliases" {
  description = "Client-facing model aliases served by the gateway."
  value       = sort(keys(var.model_routes))
}

output "backend_pool_ids" {
  description = "Resource ID of the backend pool serving each model alias."
  value       = { for alias, pool in azapi_resource.model_pool : alias => pool.id }
}

output "backend_ids" {
  description = "Resource ID of each Foundry backend registered on the gateway."
  value       = { for key, backend in azurerm_api_management_backend.foundry : key => backend.id }
}

output "products" {
  description = "Published product tiers, with the token governance applied to each."
  value = {
    for key, product in azurerm_api_management_product.this : key => {
      id                 = product.id
      product_id         = product.product_id
      display_name       = product.display_name
      tokens_per_minute  = var.products[key].tokens_per_minute
      token_quota        = var.products[key].token_quota
      token_quota_period = var.products[key].token_quota_period
      allowed_models     = length(var.products[key].allowed_models) > 0 ? var.products[key].allowed_models : sort(keys(var.model_routes))
    }
  }
}

output "subscription_keys" {
  description = <<-EOT
    Primary key per consuming application. These are the chargeback identities.

    Treat the state file as a secret store once you read this output, or better, leave subscriptions
    out of Terraform entirely in production and have consumers self-serve through the developer portal.
  EOT
  value       = { for key, sub in azurerm_api_management_subscription.this : key => sub.primary_key }
  sensitive   = true
}

output "subscription_ids" {
  description = "Resource ID of each consuming application's subscription."
  value       = { for key, sub in azurerm_api_management_subscription.this : key => sub.id }
}

output "application_insights_id" {
  description = "Application Insights resource the gateway writes its telemetry to. The cost-attribution module reads the chargeback ledger from here."
  value       = var.application_insights_id
}

output "metric_namespace" {
  description = "Custom metric namespace carrying llm-emit-token-metric output."
  value       = var.metric_namespace
}

output "cost_attribution_enabled" {
  description = "Whether the gateway emits the per-request chargeback ledger."
  value       = var.enable_cost_attribution
}

output "routes_named_value_name" {
  description = "Named value holding the base64-encoded route table. Change models here, not in policy."
  value       = azurerm_api_management_named_value.routes.name
}

output "pricing_named_value_name" {
  description = "Named value holding the base64-encoded pricing map, when cost attribution is enabled."
  value       = var.enable_cost_attribution ? azurerm_api_management_named_value.pricing[0].name : null
}
