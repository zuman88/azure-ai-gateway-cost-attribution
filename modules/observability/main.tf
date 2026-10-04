locals {
  create_workspace    = var.log_analytics_workspace_id == null
  create_app_insights = var.application_insights_id == null

  workspace_id = local.create_workspace ? azurerm_log_analytics_workspace.this[0].id : var.log_analytics_workspace_id

  common_tags = merge(var.tags, {
    "accelerator-component" = "observability"
  })
}

resource "azurerm_log_analytics_workspace" "this" {
  count = local.create_workspace ? 1 : 0

  name                = "${var.name_prefix}-law"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = var.log_analytics_sku
  retention_in_days   = var.log_retention_days
  daily_quota_gb      = var.daily_quota_gb

  tags = local.common_tags
}

resource "azurerm_application_insights" "this" {
  count = local.create_app_insights ? 1 : 0

  name                = "${var.name_prefix}-appi"
  location            = var.location
  resource_group_name = var.resource_group_name
  application_type    = "web"
  workspace_id        = local.workspace_id
  retention_in_days   = var.log_retention_days

  # See the variable documentation: sampling below 100 breaks cost attribution.
  sampling_percentage = var.application_insights_sampling_percentage

  tags = local.common_tags
}

data "azurerm_application_insights" "existing" {
  count = local.create_app_insights ? 0 : 1

  name                = element(split("/", var.application_insights_id), length(split("/", var.application_insights_id)) - 1)
  resource_group_name = element(split("/", var.application_insights_id), 4)
}
