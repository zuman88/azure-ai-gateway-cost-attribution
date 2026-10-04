terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.0.0, < 6.0.0"
    }
    # Backend pools (Microsoft.ApiManagement/service/backends with type = "Pool") are not exposed by
    # the AzureRM provider's azurerm_api_management_backend schema. See docs/decisions/0003.
    azapi = {
      source  = "Azure/azapi"
      version = ">= 2.0.0, < 3.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }
}
