variable "resource_group_name" {
  description = "Resource group that will hold the observability resources."
  type        = string
}

variable "location" {
  description = "Azure region for the observability resources. Co-locate with the gateway to avoid cross-region ingestion latency and egress."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to generated resource names."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Resource ID of an existing Log Analytics workspace to reuse. When null, a workspace is created. Reuse is the common case in brownfield engagements where a central platform workspace already exists."
  type        = string
  default     = null
}

variable "log_analytics_sku" {
  description = "Log Analytics pricing SKU, used only when a workspace is created."
  type        = string
  default     = "PerGB2018"
}

variable "log_retention_days" {
  description = <<-EOT
    Retention in days for the Log Analytics workspace and Application Insights.

    Cost attribution needs at least one full billing cycle plus reconciliation lag, so 90 days is the
    practical floor when `enable_cost_attribution` is on. Anything longer should use the workspace's
    archive tier or the optional Event Hub path rather than hot retention.
  EOT
  type        = number
  default     = 90

  validation {
    condition     = var.log_retention_days >= 30 && var.log_retention_days <= 730
    error_message = "log_retention_days must be between 30 and 730."
  }
}

variable "daily_quota_gb" {
  description = "Daily ingestion cap in GB for a created Log Analytics workspace. -1 disables the cap. A cap protects against runaway spend, but note that hitting it stops ingestion and therefore stops cost attribution for the rest of the day."
  type        = number
  default     = -1
}

variable "application_insights_id" {
  description = "Resource ID of an existing Application Insights component to reuse. When null, one is created."
  type        = string
  default     = null
}

variable "application_insights_sampling_percentage" {
  description = <<-EOT
    Sampling percentage for the Application Insights component.

    Leave this at 100 whenever cost attribution is enabled. Application Insights sampling silently drops
    telemetry, and dropped records become missing chargeback rows and understated spend. If ingestion cost
    is a concern, reduce retention or narrow which APIs log rather than sampling the LLM API.
  EOT
  type        = number
  default     = 100

  validation {
    condition     = var.application_insights_sampling_percentage > 0 && var.application_insights_sampling_percentage <= 100
    error_message = "application_insights_sampling_percentage must be greater than 0 and at most 100."
  }
}

variable "tags" {
  description = "Tags applied to all resources created by this module."
  type        = map(string)
  default     = {}
}
