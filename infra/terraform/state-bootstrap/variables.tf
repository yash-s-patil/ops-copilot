variable "subscription_id" {
  description = "Azure subscription that holds the Terraform state storage."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$", var.subscription_id))
    error_message = "subscription_id must be a lowercase GUID."
  }
}

variable "location" {
  description = "Azure region for the state storage."
  type        = string
  default     = "eastus2"
}

variable "owner" {
  description = "Value of the owner tag on every resource."
  type        = string

  validation {
    condition     = length(trimspace(var.owner)) > 0
    error_message = "owner must not be empty."
  }
}

variable "deployer_ip_cidrs" {
  description = "Public IPv4 addresses or CIDR ranges allowed to reach the state storage during bootstrap. Azure Storage rejects /31 and /32, so give a single address without a suffix. Leave empty to disable public network access once state is reached through a private endpoint."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for entry in var.deployer_ip_cidrs :
      can(regex("^(\\d{1,3}\\.){3}\\d{1,3}(/([0-9]|[12][0-9]|30))?$", entry))
    ])
    error_message = "Each entry must be an IPv4 address, or a CIDR range with a prefix of /30 or shorter."
  }
}
