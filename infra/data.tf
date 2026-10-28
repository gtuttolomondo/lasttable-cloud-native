locals {
  credential_names = toset([
    "database-admin", "database-owner", "database-api", "database-worker", "database-notify",
    "kafka-producer", "kafka-confirmation", "kafka-trending", "kafka-notifications", "kafka-observer"
  ])
}
resource "random_password" "credential" {
  for_each         = local.stage_rank >= 3 ? local.credential_names : toset([])
  length           = 30
  special          = true
  override_special = "!#%+-_="
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
}
resource "oci_vault_secret" "credential" {
  for_each       = local.stage_rank >= 3 ? local.credential_names : toset([])
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.demo[0].id
  key_id         = oci_kms_key.secrets[0].id
  secret_name    = "${var.project_name}-${each.key}"
  description    = "Dedicated LastTable ${each.key} credential"
  freeform_tags  = local.tags
  secret_content {
    content_type = "BASE64"
    content      = base64encode(random_password.credential[each.key].result)
  }
}
resource "oci_vault_secret" "kafka_admin" {
  count          = local.stage_rank >= 3 ? 1 : 0
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.demo[0].id
  key_id         = oci_kms_key.secrets[0].id
  secret_name    = "${var.project_name}-kafka-admin"
  description    = "Managed Kafka populates this secret with the generated superuser credential"
  freeform_tags  = local.tags
  secret_content {
    content_type = "BASE64"
    content      = base64encode("Awaiting managed Kafka superuser initialization")
  }
  lifecycle { ignore_changes = [secret_content] }
}
resource "oci_database_autonomous_database" "demo" {
  count                               = local.stage_rank >= 3 ? 1 : 0
  compartment_id                      = var.compartment_ocid
  display_name                        = "${var.project_name}-adb"
  db_name                             = upper("${var.project_name}db")
  db_workload                         = "OLTP"
  compute_model                       = "ECPU"
  compute_count                       = 2
  data_storage_size_in_gb             = 20
  license_model                       = "LICENSE_INCLUDED"
  is_auto_scaling_enabled             = false
  is_auto_scaling_for_storage_enabled = false
  is_free_tier                        = false
  is_mtls_connection_required         = false
  secret_id                           = oci_vault_secret.credential["database-admin"].id
  subnet_id                           = module.network.network.subnet_ids.database
  nsg_ids                             = [module.network.network.nsg_ids.database]
  private_endpoint_label              = "${var.project_name}db"
  freeform_tags                       = local.tags
  timeouts { create = "60m" }
}
resource "oci_managed_kafka_kafka_cluster_config" "demo" {
  count          = local.stage_rank >= 3 ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.project_name}-kafka-config"
  freeform_tags  = local.tags
  latest_config {
    properties = {
      "auto.create.topics.enable"                = "false"
      "num.partitions"                           = "3"
      "default.replication.factor"               = "1"
      "min.insync.replicas"                      = "1"
      "offsets.topic.replication.factor"         = "1"
      "transaction.state.log.replication.factor" = "1"
      "transaction.state.log.min.isr"            = "1"
      "log.retention.hours"                      = "168"
    }
  }
}
resource "oci_managed_kafka_kafka_cluster" "demo" {
  count                  = local.stage_rank >= 3 ? 1 : 0
  depends_on             = [oci_identity_policy.kafka_service, time_sleep.network_settle]
  compartment_id         = var.compartment_ocid
  display_name           = "${var.project_name}-kafka"
  cluster_type           = "DEVELOPMENT"
  coordination_type      = "KRAFT"
  kafka_version          = var.kafka_version
  cluster_config_id      = oci_managed_kafka_kafka_cluster_config.demo[0].id
  cluster_config_version = oci_managed_kafka_kafka_cluster_config.demo[0].latest_config[0].version_number
  access_subnets { subnets = [module.network.network.subnet_ids.kafka] }
  broker_shape {
    node_count          = 1
    node_shape          = var.kafka_broker_shape
    ocpu_count          = var.kafka_broker_ocpus
    storage_size_in_gbs = var.kafka_storage_gbs
  }
  freeform_tags = local.tags
  timeouts { create = "60m" }
}
resource "oci_managed_kafka_kafka_cluster_superusers_management" "demo" {
  count            = local.stage_rank >= 3 ? 1 : 0
  compartment_id   = var.compartment_ocid
  kafka_cluster_id = oci_managed_kafka_kafka_cluster.demo[0].id
  secret_id        = oci_vault_secret.kafka_admin[0].id
  enable_superuser = true
  timeouts { create = "30m" }
}
data "oci_managed_kafka_kafka_cluster" "ready" {
  count            = local.stage_rank >= 3 ? 1 : 0
  depends_on       = [oci_managed_kafka_kafka_cluster_superusers_management.demo]
  kafka_cluster_id = oci_managed_kafka_kafka_cluster.demo[0].id
}
resource "oci_identity_policy" "bootstrap" {
  count          = local.stage_rank >= 3 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-bootstrap-secrets"
  description    = "Temporary bootstrap workload identity can initialize only this demo's data services"
  statements = [for id in concat([for secret in oci_vault_secret.credential : secret.id], [oci_vault_secret.kafka_admin[0].id]) :
    "Allow any-user to read secret-bundles in compartment id ${var.compartment_ocid} where all {request.principal.type = 'workload', request.principal.namespace = 'lasttable-bootstrap', request.principal.service_account = 'bootstrap', request.principal.cluster_id = '${oci_containerengine_cluster.demo[0].id}', target.secret.id = '${id}'}"
  ]
  freeform_tags = local.tags
}
output "data_services" {
  value = local.stage_rank >= 3 ? {
    database_id                 = oci_database_autonomous_database.demo[0].id
    database_connection_strings = oci_database_autonomous_database.demo[0].connection_strings
    database_private_ip         = oci_database_autonomous_database.demo[0].private_endpoint_ip
    kafka_id                    = oci_managed_kafka_kafka_cluster.demo[0].id
    kafka_bootstrap_urls        = data.oci_managed_kafka_kafka_cluster.ready[0].kafka_bootstrap_urls
    kafka_admin_secret_id       = oci_vault_secret.kafka_admin[0].id
    credential_secret_ids       = { for k, s in oci_vault_secret.credential : k => s.id }
  } : null
}
