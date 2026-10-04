variable "name_prefix" {
  description = "Prefix for every resource name. Must be globally unique enough to win a Foundry custom subdomain."
  type        = string
  default     = "aigw"
}

variable "environment_name" {
  description = "Environment label, used in names, tags and telemetry."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region. Pick one where the models below are actually available; not every region carries every model."
  type        = string
  default     = "eastus"
}

variable "publisher_name" {
  description = "Publisher name shown in the API Management developer portal."
  type        = string
  default     = "AI Platform Team"
}

variable "publisher_email" {
  description = "Publisher email for API Management. Azure sends service notifications here, so use a monitored address."
  type        = string
}

variable "log_analytics_daily_quota_gb" {
  description = "Daily ingestion cap on the Log Analytics workspace. A cap is a cost guardrail, not a retention policy; data beyond it is dropped, not deferred."
  type        = number
  default     = 5
}

variable "tags" {
  description = "Additional tags."
  type        = map(string)
  default     = {}
}
