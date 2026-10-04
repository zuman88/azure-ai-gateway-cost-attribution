output "virtual_network_id" {
  description = "Resource ID of the virtual network, whether created here or supplied."
  value       = local.create_vnet ? azurerm_virtual_network.this[0].id : var.existing_virtual_network_id
}

# The depends_on is what makes this subnet safe to consume, and it is not decoration.
#
# API Management refuses to deploy into a subnet that has no network security group:
#
#   NetworkSecurityGroupNotFound: API Management service deployment into SubnetId ... requires a
#   Network Security Group to be associated with it.
#
# A caller that writes `virtual_network_subnet_id = module.network.apim_subnet_id` creates a
# dependency on the subnet alone. The NSG association is a separate resource that nothing downstream
# references, so Terraform is free to create API Management while the association is still in flight,
# and the deployment fails roughly half the time - the worst kind of bug, because re-running appears
# to fix it. Hanging the dependency off the output makes every consumer wait, without any of them
# having to know the rule exists.
output "apim_subnet_id" {
  description = "Subnet to hand to the ai-gateway module as virtual_network_subnet_id. Ordered behind its NSG and NAT gateway associations."
  value       = local.apim_subnet_id

  depends_on = [
    azurerm_subnet_network_security_group_association.apim,
    azurerm_subnet_nat_gateway_association.apim,
  ]
}

output "private_endpoint_subnet_id" {
  description = "Subnet to hand to the foundry-models module for its private endpoints. Ordered behind its NSG association."
  value       = local.pe_subnet_id

  depends_on = [azurerm_subnet_network_security_group_association.private_endpoints]
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
