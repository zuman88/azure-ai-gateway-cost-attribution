# ===================================================================================================
# networking - inputs
# ===================================================================================================

variable "resource_group_name" {
  description = "Resource group for the network resources."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to resource names."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "name_prefix must be 2-21 characters of lowercase letters, digits or hyphens."
  }
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------------------------------
# Virtual network
# ---------------------------------------------------------------------------------------------------

variable "existing_virtual_network_id" {
  description = "Use an existing virtual network instead of creating one. When set, the subnet variables below must reference subnets that already exist in it."
  type        = string
  default     = null
}

variable "address_space" {
  description = "Address space of the virtual network this module creates."
  type        = list(string)
  default     = ["10.60.0.0/16"]
}

variable "apim_subnet_address_prefix" {
  description = <<-EOT
    Subnet delegated to API Management.

    The v2 tiers use outbound VNet integration and require the subnet to be delegated to
    Microsoft.Web/serverFarms, with a minimum size of /27. The classic tiers require no delegation but
    do require a /27 or larger and an NSG that permits the management endpoint. Both are handled below.
  EOT
  type        = string
  default     = "10.60.1.0/27"
}

variable "private_endpoint_subnet_address_prefix" {
  description = "Subnet holding the private endpoints for Foundry and any other PaaS dependency."
  type        = string
  default     = "10.60.2.0/24"
}

variable "existing_apim_subnet_id" {
  description = "Existing subnet for API Management. Required when existing_virtual_network_id is set."
  type        = string
  default     = null
}

variable "existing_private_endpoint_subnet_id" {
  description = "Existing subnet for private endpoints. Required when existing_virtual_network_id is set."
  type        = string
  default     = null
}

variable "apim_sku_name" {
  description = "API Management SKU the subnet is being prepared for. Determines whether the subnet is delegated and which NSG rules are required."
  type        = string
  default     = "StandardV2_1"
}

variable "apim_virtual_network_type" {
  description = "How API Management attaches to the network: None, External or Internal. Only used to decide which NSG rules the classic tiers need."
  type        = string
  default     = "External"

  validation {
    condition     = contains(["None", "External", "Internal"], var.apim_virtual_network_type)
    error_message = "apim_virtual_network_type must be None, External or Internal."
  }
}

# ---------------------------------------------------------------------------------------------------
# Private DNS
# ---------------------------------------------------------------------------------------------------

variable "create_private_dns_zones" {
  description = <<-EOT
    Create and link the private DNS zones that Foundry private endpoints need.

    Set this to false when the zones are owned centrally, which is the norm in a hub-and-spoke estate,
    and supply existing_private_dns_zone_ids instead. Creating a duplicate zone in a spoke is one of
    the more effective ways to break name resolution across an entire landing zone.
  EOT
  type        = bool
  default     = true
}

variable "existing_private_dns_zone_ids" {
  description = <<-EOT
    Resource IDs of pre-existing private DNS zones, keyed by zone name.

    All three of these are required for Foundry. A private endpoint registers records in more than one
    of them depending on which hostname the client uses, and a missing zone produces failures that are
    intermittent rather than absolute - which makes them considerably harder to diagnose:

      privatelink.cognitiveservices.azure.com
      privatelink.openai.azure.com
      privatelink.services.ai.azure.com
  EOT
  type        = map(string)
  default     = {}
}

variable "private_dns_zone_resource_group_name" {
  description = "Resource group to create the private DNS zones in. Defaults to resource_group_name."
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------------------------------
# Egress
# ---------------------------------------------------------------------------------------------------

variable "enable_nat_gateway" {
  description = <<-EOT
    Attach a NAT gateway to the API Management subnet so that outbound traffic leaves from a stable,
    known address.

    Worth the cost in two situations: a Foundry or partner endpoint that allow-lists source addresses,
    and any workload large enough to exhaust SNAT ports on the platform's shared outbound path.
  EOT
  type        = bool
  default     = false
}

variable "enable_service_endpoints" {
  description = "Add service endpoints for Cognitive Services and Key Vault to the API Management subnet. Harmless when private endpoints are used, and the fallback when they are not."
  type        = bool
  default     = true
}
