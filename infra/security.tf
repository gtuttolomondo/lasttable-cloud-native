locals {
  stage_rank = index(["network", "security", "platform", "data", "delivery", "application"], var.deployment_stage)
}
resource "oci_kms_vault" "demo" {
  count          = local.stage_rank >= 1 ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.project_name}-vault"
  vault_type     = "DEFAULT"
  freeform_tags  = local.tags
}
# Vault control-plane ACTIVE can precede its management endpoint DNS record.
resource "time_sleep" "vault_settle" {
  count           = local.stage_rank >= 1 ? 1 : 0
  depends_on      = [oci_kms_vault.demo]
  create_duration = "60s"
}
resource "oci_kms_key" "secrets" {
  depends_on          = [time_sleep.vault_settle]
  count               = local.stage_rank >= 1 ? 1 : 0
  compartment_id      = var.compartment_ocid
  display_name        = "${var.project_name}-secrets"
  management_endpoint = oci_kms_vault.demo[0].management_endpoint
  protection_mode     = "SOFTWARE"
  freeform_tags       = local.tags
  key_shape {
    algorithm = "AES"
    length    = 32
  }
}
resource "oci_logging_log_group" "demo" {
  count          = local.stage_rank >= 1 ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.project_name}-logs"
  description    = "LastTable demo service and application logs"
  freeform_tags  = local.tags
}
resource "oci_identity_policy" "kafka_service" {
  count          = local.stage_rank >= 1 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-kafka-service"
  description    = "Managed Kafka private endpoints and SCRAM secret metadata access in the demo compartment"
  statements = [
    "Allow service rawfka to use vnics in compartment id ${var.compartment_ocid}",
    "Allow service rawfka to use network-security-groups in compartment id ${var.compartment_ocid}",
    "Allow service rawfka to use subnets in compartment id ${var.compartment_ocid}",
    "Allow service rawfka to read secrets in compartment id ${var.compartment_ocid}",
    "Allow service rawfka to use secrets in compartment id ${var.compartment_ocid} where request.operation = 'UpdateSecret'"
  ]
  freeform_tags = local.tags
}
resource "time_sleep" "network_settle" {
  count           = local.stage_rank >= 1 ? 1 : 0
  depends_on      = [module.network]
  create_duration = "60s"
}
resource "oci_bastion_bastion" "admin" {
  depends_on                   = [time_sleep.network_settle]
  count                        = local.stage_rank >= 1 ? 1 : 0
  bastion_type                 = "STANDARD"
  compartment_id               = var.compartment_ocid
  name                         = "${var.project_name}admin"
  target_subnet_id             = module.network.network.subnet_ids.automation
  client_cidr_block_allow_list = var.admin_client_cidrs
  max_session_ttl_in_seconds   = 10800
  freeform_tags                = local.tags
  lifecycle {
    precondition {
      condition     = length(var.admin_client_cidrs) > 0
      error_message = "Provide at least one explicit IPv4 /32 client address for Bastion."
    }
  }
}
output "security" {
  value = local.stage_rank >= 1 ? {
    vault_id     = oci_kms_vault.demo[0].id
    key_id       = oci_kms_key.secrets[0].id
    bastion_id   = oci_bastion_bastion.admin[0].id
    log_group_id = oci_logging_log_group.demo[0].id
  } : null
}
