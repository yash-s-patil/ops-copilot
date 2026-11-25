locals {
  name_suffix                   = "opscp-tfstate"
  soft_delete_retention_in_days = 30

  tags = {
    project     = "ops-copilot"
    environment = "shared"
    owner       = var.owner
    managed_by  = "terraform"
  }
}

data "azurerm_client_config" "current" {}

resource "random_string" "storage_suffix" {
  length  = 6
  lower   = true
  numeric = true
  upper   = false
  special = false
}

resource "azurerm_resource_group" "state" {
  name     = "rg-${local.name_suffix}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "state" {
  # checkov:skip=CKV_AZURE_59: State is needed before any virtual network exists; access is limited to deployer addresses by network_rules.
  # checkov:skip=CKV2_AZURE_33: Same reason; a private endpoint needs the network this state is used to create.
  # checkov:skip=CKV_AZURE_36: Trusted-service bypass is deliberately off; nothing but the deployer reads state.
  # checkov:skip=CKV2_AZURE_1: A customer-managed key needs a Key Vault, which is created from this state.
  # checkov:skip=CKV_AZURE_33: The queue service is not used.
  name                = "st${replace(local.name_suffix, "-", "")}${random_string.storage_suffix.result}"
  resource_group_name = azurerm_resource_group.state.name
  location            = azurerm_resource_group.state.location
  tags                = local.tags

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "GRS"

  min_tls_version                  = "TLS1_2"
  https_traffic_only_enabled       = true
  shared_access_key_enabled        = false
  default_to_oauth_authentication  = true
  local_user_enabled               = false
  allow_nested_items_to_be_public  = false
  cross_tenant_replication_enabled = false

  public_network_access = "Enabled"

  network_rules {
    default_action = "Deny"
    bypass         = ["None"]
    ip_rules       = var.deployer_ip_cidrs
  }

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = local.soft_delete_retention_in_days
    }

    container_delete_retention_policy {
      days = local.soft_delete_retention_in_days
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "state" {
  # checkov:skip=CKV2_AZURE_21: Read logging needs a Log Analytics workspace, which is created from this state.
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_role_assignment" "deployer_state_access" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}
