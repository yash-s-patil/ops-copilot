output "resource_group_name" {
  description = "Resource group that holds the state storage."
  value       = azurerm_resource_group.state.name
}

output "storage_account_name" {
  description = "Storage account to use in the azurerm backend configuration."
  value       = azurerm_storage_account.state.name
}

output "container_name" {
  description = "Blob container to use in the azurerm backend configuration."
  value       = azurerm_storage_container.state.name
}
