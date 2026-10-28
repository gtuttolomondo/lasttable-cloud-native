variable "compartment_ocid" {
  description = "Existing compartment in which all demo resources are created."
  type        = string
  validation {
    condition     = can(regex("^ocid1.compartment\\.", var.compartment_ocid))
    error_message = "Select an existing compartment OCID."
  }
}
variable "region" {
  description = "OCI deployment region."
  type        = string
}
variable "project_name" {
  type    = string
  default = "lasttable"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,11}$", var.project_name))
    error_message = "Use 3-12 lowercase alphanumeric characters, starting with a letter."
  }
}
variable "vcn_cidr" {
  type    = string
  default = "10.42.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vcn_cidr)) && can(regex("/16$", var.vcn_cidr))
    error_message = "Provide an IPv4 /16 network."
  }
}
variable "deployment_stage" {
  description = "Cumulative stages. Never decrease this value on an existing stack."
  type        = string
  default     = "network"
  validation {
    condition     = contains(["network", "security", "platform", "data", "delivery", "application"], var.deployment_stage)
    error_message = "Select a supported cumulative deployment stage."
  }
}
variable "demo_domain" {
  description = "Registered public domain delegated to the new OCI DNS zone."
  type        = string
  default     = "example.com"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", var.demo_domain))
    error_message = "Provide a registered domain without protocol or path."
  }
}
variable "smtp_user_ocid" {
  description = "Existing IAM user with an available SMTP credential slot (maximum two)."
  type        = string
  default     = ""
}
variable "notification_image" {
  description = "Tested private OCIR notification image URI with a unique release tag."
  type        = string
  default     = ""
}
variable "notification_image_digest" {
  description = "Authoritative OCIR sha256 digest for the tested notification image."
  type        = string
  default     = ""
}
variable "admin_client_cidrs" {
  description = "Public IPv4 /32 addresses allowed to establish temporary Bastion sessions."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for c in var.admin_client_cidrs : can(cidrnetmask(c)) && can(regex("/32$", c))])
    error_message = "Bastion clients must be specific IPv4 /32 addresses."
  }
}
variable "tenancy_ocid" {
  description = "Tenancy identifier used only for region/availability-domain discovery."
  type        = string
}
variable "kubernetes_version" {
  type    = string
  default = "v1.36.4"
}
variable "node_shape" {
  type    = string
  default = "VM.Standard.E4.Flex"
}
variable "node_count" {
  type    = number
  default = 2
  validation {
    condition     = var.node_count >= 1 && var.node_count <= 4 && floor(var.node_count) == var.node_count
    error_message = "The demo supports one to four managed nodes."
  }
}
variable "node_ocpus" {
  type    = number
  default = 1
}
variable "node_memory_gbs" {
  type    = number
  default = 12
}
variable "node_image_name" {
  description = "Pinned amd64 OKE image name. Resolve the regional image OCID through OKE options."
  type        = string
  default     = "Oracle-Linux-9.8-2026.09.18-0-OKE-1.36.4-1923"
}
variable "kafka_version" {
  type    = string
  default = "4.0.0"
}
variable "kafka_broker_shape" {
  type    = string
  default = "VM.Standard.A1.Flex"
}
variable "kafka_broker_ocpus" {
  type    = number
  default = 1
}
variable "kafka_storage_gbs" {
  type    = number
  default = 50
}
variable "existing_smtp_secret_ids" {
  description = "Optional existing Vault SMTP secret references; avoids creating another IAM SMTP credential."
  type        = map(string)
  default     = {}
  validation {
    condition = length(var.existing_smtp_secret_ids) == 0 || (
      toset(keys(var.existing_smtp_secret_ids)) == toset(["username", "password"]) &&
      alltrue([for id in values(var.existing_smtp_secret_ids) : can(regex("^ocid1.vaultsecret\\.", id))])
    )
    error_message = "Supply both username and password Vault secret OCIDs, or leave the map empty."
  }
}
variable "parent_dns_zone_id" {
  description = "Optional parent OCI DNS zone for automatic delegation of a child demo domain."
  type        = string
  default     = ""
}

variable "table_admin_secret_name" {
  description = "Existing Kubernetes Secret with key code, provisioned privately by the operator. No secret value enters Terraform."
  type        = string
  default     = "table-admin-access"
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.table_admin_secret_name))
    error_message = "Use a valid Kubernetes Secret name."
  }
}
