variable "resource_group_name" {
  description = "Name of the resource group that will hold the Foundry accounts."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every generated resource name. Keep it short; Foundry account names are limited to 64 characters and must be globally unique when a custom subdomain is used."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "name_prefix must be 2-21 characters, lowercase alphanumeric or hyphen, and start with a letter or digit."
  }
}

variable "accounts" {
  description = <<-EOT
    Foundry accounts (Microsoft.CognitiveServices/accounts, kind = AIServices) to create, keyed by a short
    logical name such as "eastus-std" or "eastus-ptu".

    Every account receives the full `model_deployments` set by default. That deployment-name parity is what
    allows a single APIM backend pool to serve a single model alias, so deviate from it only deliberately
    via `excluded_deployments`.

    * location              - Azure region for the account.
    * sku_name              - Cognitive Services SKU. "S0" is the only generally available option.
    * custom_subdomain_name - Optional explicit subdomain. Defaults to "<name_prefix>-<key>". A custom
                              subdomain is mandatory for Entra ID (managed identity) authentication and
                              for private endpoints.
    * public_network_access_enabled - Set false when fronting the account with a private endpoint.
    * excluded_deployments  - Deployment keys this account must NOT host. Breaks pool parity; see README.
    * deployment_overrides  - Per-account SKU/capacity overrides, e.g. reserved (PTU) capacity in one region
                              and pay-as-you-go elsewhere.
  EOT

  type = map(object({
    location                      = string
    sku_name                      = optional(string, "S0")
    custom_subdomain_name         = optional(string)
    public_network_access_enabled = optional(bool, true)
    excluded_deployments          = optional(list(string), [])
    deployment_overrides = optional(map(object({
      sku_name = optional(string)
      capacity = optional(number)
    })), {})
    tags = optional(map(string), {})
  }))

  validation {
    condition     = length(var.accounts) > 0
    error_message = "At least one Foundry account must be defined."
  }

  validation {
    condition     = alltrue([for k, v in var.accounts : can(regex("^[a-z0-9][a-z0-9-]{1,30}$", k))])
    error_message = "Account keys must be 2-31 characters, lowercase alphanumeric or hyphen."
  }
}

variable "model_deployments" {
  description = <<-EOT
    Model deployments created on every account, keyed by the physical deployment name that APIM will place
    in the request path. Keys are the contract between this module and the ai-gateway module.

    * model_name    - Publisher model name, e.g. "gpt-4o", "text-embedding-3-large", "Mistral-Large-2411".
    * model_format  - Publisher. "OpenAI" for Azure OpenAI models, "Mistral AI", "Meta", "Microsoft", etc.
    * model_version - Publisher model version. Pin it; "latest" makes cost and behaviour unpredictable.
    * sku_name      - Deployment SKU: GlobalStandard, Standard, DataZoneStandard, GlobalProvisionedManaged,
                      ProvisionedManaged. Use DataZoneStandard where data residency is constrained.
    * capacity      - Units of the chosen SKU. For Standard SKUs this is thousands of tokens per minute.
  EOT

  type = map(object({
    model_name                 = string
    model_format               = optional(string, "OpenAI")
    model_version              = string
    sku_name                   = optional(string, "GlobalStandard")
    capacity                   = optional(number, 10)
    rai_policy_name            = optional(string)
    version_upgrade_option     = optional(string, "OnceCurrentVersionExpired")
    dynamic_throttling_enabled = optional(bool, false)
  }))

  validation {
    condition = alltrue([
      for k, v in var.model_deployments :
      contains(["GlobalStandard", "Standard", "DataZoneStandard", "GlobalProvisionedManaged", "ProvisionedManaged", "GlobalBatch", "DataZoneBatch"], v.sku_name)
    ])
    error_message = "sku_name must be one of GlobalStandard, Standard, DataZoneStandard, GlobalProvisionedManaged, ProvisionedManaged, GlobalBatch, DataZoneBatch."
  }

  validation {
    condition     = alltrue([for k, v in var.model_deployments : v.model_version != "latest"])
    error_message = "Pin model_version to an explicit publisher version. Using 'latest' makes cost and behaviour change without a Terraform diff."
  }

  validation {
    condition = alltrue([
      for k, v in var.model_deployments :
      contains(["OnceNewDefaultVersionAvailable", "OnceCurrentVersionExpired", "NoAutoUpgrade"], v.version_upgrade_option)
    ])
    error_message = "version_upgrade_option must be OnceNewDefaultVersionAvailable, OnceCurrentVersionExpired, or NoAutoUpgrade."
  }
}

variable "local_auth_enabled" {
  description = "Whether key-based authentication is permitted on the Foundry accounts. Leave false so that managed identity is the only possible access path."
  type        = bool
  default     = false
}

variable "gateway_principal_ids" {
  description = "Object IDs of the principals (normally the API Management managed identity) granted data-plane access to every Foundry account."
  type        = list(string)
  default     = []
}

variable "data_plane_role" {
  description = "Built-in role granted to gateway_principal_ids. 'Cognitive Services User' covers inference across Foundry model families; 'Cognitive Services OpenAI User' is narrower and covers only Azure OpenAI."
  type        = string
  default     = "Cognitive Services User"

  validation {
    condition     = contains(["Cognitive Services User", "Cognitive Services OpenAI User"], var.data_plane_role)
    error_message = "data_plane_role must be 'Cognitive Services User' or 'Cognitive Services OpenAI User'."
  }
}

variable "private_endpoint_subnet_id" {
  description = "Subnet resource ID for Foundry private endpoints. When null, no private endpoints are created."
  type        = string
  default     = null
}

variable "private_dns_zone_ids" {
  description = <<-EOT
    Private DNS zone resource IDs to associate with the Foundry private endpoints.

    Supply all three zones. A Foundry account answers on cognitiveservices, openai, and services.ai
    hostnames, and omitting any one of them produces intermittent resolution failures that are
    unpleasant to diagnose:
      privatelink.cognitiveservices.azure.com
      privatelink.openai.azure.com
      privatelink.services.ai.azure.com
  EOT
  type        = list(string)
  default     = []
}

variable "diagnostics_workspace_id" {
  description = "Log Analytics workspace resource ID for Foundry diagnostic settings. When null, no diagnostic settings are created."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags applied to all resources created by this module."
  type        = map(string)
  default     = {}
}
