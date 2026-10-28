locals {
  workload_secret_roles = {
    api           = ["database-api", "demo-admin"]
    publisher     = ["database-worker", "kafka-producer"]
    confirmation  = ["database-worker", "kafka-confirmation"]
    trending      = ["database-worker", "kafka-trending"]
    notifications = ["database-worker", "kafka-notifications"]
    observer      = ["database-worker", "kafka-observer"]
    replay        = ["database-worker", "kafka-trending"]
  }
  application_secret_ids = local.stage_rank >= 4 ? merge(
    { for k, s in oci_vault_secret.credential : k => s.id },
    { demo-admin = oci_vault_secret.demo_admin[0].id }
  ) : {}
  workload_conditions = local.stage_rank >= 4 ? {
    for role in concat(keys(local.workload_secret_roles), ["registry"]) : role => "request.principal.type = 'workload', request.principal.namespace = 'lasttable', request.principal.service_account = '${role}', request.principal.cluster_id = '${oci_containerengine_cluster.demo[0].id}'"
  } : {}
}
resource "oci_identity_policy" "workloads" {
  for_each       = local.stage_rank >= 4 ? local.workload_secret_roles : {}
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-workload-${each.key}"
  description    = "Exact OKE workload identity and role-specific secret bundles"
  statements = concat(
    [for role in each.value : "Allow any-user to read secret-bundles in compartment id ${var.compartment_ocid} where all {${local.workload_conditions[each.key]}, target.secret.id = '${local.application_secret_ids[role]}'}"],
    each.key == "notifications" ? ["Allow any-user to use fn-invocation in compartment id ${var.compartment_ocid} where all {${local.workload_conditions[each.key]}, target.function.id = '${oci_functions_function.notifications[0].id}'}"] : []
  )
  freeform_tags = local.tags
}
resource "oci_identity_policy" "registry_workload" {
  count          = local.stage_rank >= 4 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-registry-pull"
  description    = "Registry credential refresh with a scoped OKE workload identity"
  statements     = ["Allow any-user to read repos in compartment id ${var.compartment_ocid} where all {${local.workload_conditions["registry"]}}"]
  freeform_tags  = local.tags
}
