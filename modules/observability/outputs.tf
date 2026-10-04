output "log_analytics_workspace_id" {
  description = "Resource ID of the Log Analytics workspace in use, whether created here or supplied."
  value       = local.workspace_id
}

output "log_analytics_workspace_name" {
  description = "Name of the Log Analytics workspace, when it was created by this module."
  value       = local.create_workspace ? azurerm_log_analytics_workspace.this[0].name : null
}

output "application_insights_id" {
  description = "Resource ID of the Application Insights component in use."
  value       = local.create_app_insights ? azurerm_application_insights.this[0].id : var.application_insights_id
}

output "application_insights_name" {
  description = "Name of the Application Insights component in use."
  value       = local.create_app_insights ? azurerm_application_insights.this[0].name : data.azurerm_application_insights.existing[0].name
}

output "application_insights_app_id" {
  description = "Application Insights application ID, used by the reconciliation scripts to query the chargeback ledger."
  value       = local.create_app_insights ? azurerm_application_insights.this[0].app_id : data.azurerm_application_insights.existing[0].app_id
}

output "application_insights_instrumentation_key" {
  description = "Instrumentation key consumed by the API Management logger."
  value       = local.create_app_insights ? azurerm_application_insights.this[0].instrumentation_key : data.azurerm_application_insights.existing[0].instrumentation_key
  sensitive   = true
}

output "application_insights_connection_string" {
  description = "Application Insights connection string."
  value       = local.create_app_insights ? azurerm_application_insights.this[0].connection_string : data.azurerm_application_insights.existing[0].connection_string
  sensitive   = true
}
