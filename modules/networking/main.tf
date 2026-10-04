# ===================================================================================================
# networking
#
# The network the private deployment topology sits on: a virtual network, a subnet prepared correctly
# for whichever API Management tier is in use, a subnet for private endpoints, and the three private
# DNS zones Foundry needs.
#
# This module is optional. Skip it when a platform team already owns the landing zone and feed their
# subnet and zone IDs into the other modules instead - duplicating a private DNS zone inside a spoke
# is a reliable way to break name resolution for everything else in it.
# ===================================================================================================

locals {
  tags = merge(var.tags, { "module" = "networking" })

  create_vnet = var.existing_virtual_network_id == null

  # The v2 tiers integrate outbound through a delegated subnet; the classic tiers inject the service
  # itself into an undelegated subnet. The delegation is not cosmetic - the platform refuses the
  # deployment if it is wrong in either direction.
  is_v2_sku = can(regex("V2_", var.apim_sku_name))

  dns_zone_names = [
    "privatelink.cognitiveservices.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.services.ai.azure.com",
  ]

  dns_zone_rg = coalesce(var.private_dns_zone_resource_group_name, var.resource_group_name)

  apim_subnet_id = local.create_vnet ? azurerm_subnet.apim[0].id : var.existing_apim_subnet_id
  pe_subnet_id   = local.create_vnet ? azurerm_subnet.private_endpoints[0].id : var.existing_private_endpoint_subnet_id

  private_dns_zone_ids = var.create_private_dns_zones ? {
    for name, zone in azurerm_private_dns_zone.foundry : name => zone.id
  } : var.existing_private_dns_zone_ids

  service_endpoints = var.enable_service_endpoints ? ["Microsoft.CognitiveServices", "Microsoft.KeyVault", "Microsoft.Storage"] : []
}

resource "terraform_data" "guards" {
  input = local.create_vnet

  lifecycle {
    precondition {
      condition     = local.create_vnet || (var.existing_apim_subnet_id != null && var.existing_private_endpoint_subnet_id != null)
      error_message = "existing_apim_subnet_id and existing_private_endpoint_subnet_id are both required when existing_virtual_network_id is set."
    }

    precondition {
      condition = var.create_private_dns_zones || alltrue([
        for name in local.dns_zone_names : contains(keys(var.existing_private_dns_zone_ids), name)
      ])
      error_message = "When create_private_dns_zones is false, existing_private_dns_zone_ids must contain all three Foundry zones: ${join(", ", local.dns_zone_names)}. A Foundry private endpoint registers records across all of them, and a missing zone fails intermittently rather than outright."
    }
  }
}

# ---------------------------------------------------------------------------------------------------
# Virtual network and subnets
# ---------------------------------------------------------------------------------------------------
resource "azurerm_virtual_network" "this" {
  count = local.create_vnet ? 1 : 0

  name                = "${var.name_prefix}-vnet"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = var.address_space
  tags                = local.tags
}

resource "azurerm_subnet" "apim" {
  count = local.create_vnet ? 1 : 0

  name                 = "snet-apim"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = [var.apim_subnet_address_prefix]

  dynamic "service_endpoint" {
    for_each = toset(local.service_endpoints)
    content {
      service = service_endpoint.value
    }
  }

  dynamic "delegation" {
    for_each = local.is_v2_sku ? [1] : []
    content {
      name = "apim-v2-outbound"
      service_delegation {
        name    = "Microsoft.Web/serverFarms"
        actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
      }
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  count = local.create_vnet ? 1 : 0

  name                 = "snet-private-endpoints"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = [var.private_endpoint_subnet_address_prefix]

  private_endpoint_network_policies = "Enabled"
}

# ---------------------------------------------------------------------------------------------------
# Network security
#
# The classic tiers inject the service into the subnet and need the control-plane rules below or the
# deployment degrades in ways that are hard to attribute. The v2 tiers integrate outbound only and need
# none of it, so the rules are omitted rather than applied "just in case" - an unnecessary allow rule is
# still an allow rule somebody has to justify at review time.
# ---------------------------------------------------------------------------------------------------
resource "azurerm_network_security_group" "apim" {
  count = local.create_vnet ? 1 : 0

  name                = "${var.name_prefix}-apim-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = local.tags
}

resource "azurerm_network_security_rule" "apim_management" {
  count = local.create_vnet && !local.is_v2_sku ? 1 : 0

  name = "AllowApiManagementControlPlane"
  # Without this rule a classic-tier instance reports as unhealthy and configuration updates silently
  # stop applying, which is a genuinely unpleasant failure to diagnose after the fact.
  description                 = "Required by the classic tiers so that Azure can manage the injected service."
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.apim[0].name
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "3443"
  source_address_prefix       = "ApiManagement"
  destination_address_prefix  = "VirtualNetwork"
}

resource "azurerm_network_security_rule" "apim_load_balancer" {
  count = local.create_vnet && !local.is_v2_sku ? 1 : 0

  name                        = "AllowAzureLoadBalancerHealthProbe"
  description                 = "Health probes from the platform load balancer."
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.apim[0].name
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "6390"
  source_address_prefix       = "AzureLoadBalancer"
  destination_address_prefix  = "VirtualNetwork"
}

resource "azurerm_network_security_rule" "apim_client_https" {
  count = local.create_vnet && !local.is_v2_sku && var.apim_virtual_network_type == "External" ? 1 : 0

  name                        = "AllowClientHttps"
  description                 = "Client traffic to the gateway when the instance is externally reachable."
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.apim[0].name
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = "Internet"
  destination_address_prefix  = "VirtualNetwork"
}

resource "azurerm_subnet_network_security_group_association" "apim" {
  count = local.create_vnet ? 1 : 0

  subnet_id                 = azurerm_subnet.apim[0].id
  network_security_group_id = azurerm_network_security_group.apim[0].id
}

# ---------------------------------------------------------------------------------------------------
# Private endpoint subnet security
#
# A subnet with no NSG inherits only the platform defaults, which permit any VNet-sourced traffic to
# reach it. That is a wide blast radius for the one subnet holding private links to Foundry, Key Vault
# and storage: anything that gains a foothold anywhere in the VNet, or in a peered VNet, can reach
# every private endpoint in it.
#
# Note that NSG rules only take effect on private endpoints because private_endpoint_network_policies
# is "Enabled" on the subnet above. With it disabled - which was the default for years - these rules
# would be accepted by Terraform and silently never evaluated.
# ---------------------------------------------------------------------------------------------------
resource "azurerm_network_security_group" "private_endpoints" {
  count = local.create_vnet ? 1 : 0

  name                = "${var.name_prefix}-pe-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = local.tags
}

resource "azurerm_network_security_rule" "pe_allow_vnet_https" {
  count = local.create_vnet ? 1 : 0

  name                        = "AllowVnetHttpsInbound"
  description                 = "Private endpoints are reached over 443 from inside the virtual network."
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.private_endpoints[0].name
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = "VirtualNetwork"
  destination_address_prefix  = "VirtualNetwork"
}

# The platform's own DenyAllInBound sits at priority 65500, *below* AllowVnetInBound at 65000. Without
# an explicit rule here, that default allow wins and the 443-only rule above constrains nothing.
resource "azurerm_network_security_rule" "pe_deny_inbound" {
  count = local.create_vnet ? 1 : 0

  name                        = "DenyAllInbound"
  description                 = "Everything other than the HTTPS rule above is denied."
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.private_endpoints[0].name
  priority                    = 4096
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
}

resource "azurerm_subnet_network_security_group_association" "private_endpoints" {
  count = local.create_vnet ? 1 : 0

  subnet_id                 = azurerm_subnet.private_endpoints[0].id
  network_security_group_id = azurerm_network_security_group.private_endpoints[0].id
}

# ---------------------------------------------------------------------------------------------------
# Private DNS
#
# All three zones, every time. Which one answers depends on the hostname the caller used, and Foundry
# is reachable under all three. Creating only the obvious one produces a gateway that works until
# somebody changes a base URL.
# ---------------------------------------------------------------------------------------------------
resource "azurerm_private_dns_zone" "foundry" {
  for_each = var.create_private_dns_zones ? toset(local.dns_zone_names) : toset([])

  name                = each.value
  resource_group_name = local.dns_zone_rg
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "foundry" {
  for_each = var.create_private_dns_zones && local.create_vnet ? toset(local.dns_zone_names) : toset([])

  name                 = "${var.name_prefix}-link"
  private_dns_zone_id  = azurerm_private_dns_zone.foundry[each.value].id
  virtual_network_id   = azurerm_virtual_network.this[0].id
  registration_enabled = false
  tags                 = local.tags
}

# ---------------------------------------------------------------------------------------------------
# Stable egress (optional)
# ---------------------------------------------------------------------------------------------------
resource "azurerm_public_ip" "nat" {
  count = var.enable_nat_gateway && local.create_vnet ? 1 : 0

  name                = "${var.name_prefix}-nat-pip"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = ["1", "2", "3"]
  tags                = local.tags
}

resource "azurerm_nat_gateway" "this" {
  count = var.enable_nat_gateway && local.create_vnet ? 1 : 0

  name                    = "${var.name_prefix}-nat"
  location                = var.location
  resource_group_name     = var.resource_group_name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
  tags                    = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  count = var.enable_nat_gateway && local.create_vnet ? 1 : 0

  nat_gateway_id       = azurerm_nat_gateway.this[0].id
  public_ip_address_id = azurerm_public_ip.nat[0].id
}

resource "azurerm_subnet_nat_gateway_association" "apim" {
  count = var.enable_nat_gateway && local.create_vnet ? 1 : 0

  subnet_id      = azurerm_subnet.apim[0].id
  nat_gateway_id = azurerm_nat_gateway.this[0].id
}
