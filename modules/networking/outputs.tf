output "virtual_network_id" {
  description = "Resource ID of the virtual network, whether created here or supplied."
  value       = local.create_vnet ? azurerm_virtual_network.this[0].id : var.existing_virtual_network_id
}

output "apim_subnet_id" {
  description = "Subnet to hand to the ai-gateway module as virtual_network_subnet_id."
  value       = local.apim_subnet_id
}

output "private_endpoint_subnet_id" {
  description = "Subnet to hand to the foundry-models module for its private endpoints."
  value       = local.pe_subnet_id
}

output "private_dns_zone_ids" {
  description = <<-EOT
    All three Foundry private DNS zones, keyed by zone name. Pass the whole map to the foundry-models
    module: a private endpoint needs records in each of them, because which zone answers depends on
    which hostname the caller used.
  EOT
  value       = local.private_dns_zone_ids
}

output "private_dns_zone_ids_list" {
  description = "The same zones as a list, for consumers that take an unkeyed collection."
  value       = values(local.private_dns_zone_ids)
}

output "network_security_group_id" {
  description = "Network security group attached to the API Management subnet, when this module created the network."
  value       = local.create_vnet ? azurerm_network_security_group.apim[0].id : null
}

output "nat_gateway_public_ip" {
  description = "Stable outbound address of the gateway, when the NAT gateway is enabled. This is the address to give anyone who needs to allow-list your egress."
  value       = var.enable_nat_gateway && local.create_vnet ? azurerm_public_ip.nat[0].ip_address : null
}
