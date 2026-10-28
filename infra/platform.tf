data "oci_identity_availability_domains" "region" {
  count          = local.stage_rank >= 2 ? 1 : 0
  compartment_id = var.tenancy_ocid
}
data "oci_containerengine_cluster_option" "region" {
  count             = local.stage_rank >= 2 ? 1 : 0
  cluster_option_id = "all"
}
data "oci_containerengine_node_pool_option" "region" {
  count               = local.stage_rank >= 2 ? 1 : 0
  node_pool_option_id = "all"
  compartment_id      = var.compartment_ocid
}
locals {
  image_by_name = local.stage_rank >= 2 ? { for s in data.oci_containerengine_node_pool_option.region[0].sources : s.source_name => s.image_id } : {}
}
resource "oci_containerengine_cluster" "demo" {
  count              = local.stage_rank >= 2 ? 1 : 0
  depends_on         = [time_sleep.network_settle]
  compartment_id     = var.compartment_ocid
  name               = "${var.project_name}-oke"
  kubernetes_version = var.kubernetes_version
  vcn_id             = module.network.network.vcn_id
  type               = "ENHANCED_CLUSTER"
  freeform_tags      = local.tags
  cluster_pod_network_options { cni_type = "FLANNEL_OVERLAY" }
  endpoint_config {
    is_public_ip_enabled = false
    subnet_id            = module.network.network.subnet_ids.api
    nsg_ids              = [module.network.network.nsg_ids.api]
  }
  options {
    service_lb_subnet_ids = [module.network.network.subnet_ids.ingress]
    kubernetes_network_config {
      pods_cidr     = "10.244.0.0/16"
      services_cidr = "10.96.0.0/16"
    }
    add_ons {
      is_kubernetes_dashboard_enabled = false
      is_tiller_enabled               = false
    }
    service_lb_config { freeform_tags = local.tags }
  }
  lifecycle {
    precondition {
      condition     = contains(data.oci_containerengine_cluster_option.region[0].kubernetes_versions, var.kubernetes_version)
      error_message = "The requested Kubernetes version is not available in this region."
    }
  }
  timeouts { create = "60m" }
}
resource "oci_containerengine_node_pool" "demo" {
  count              = local.stage_rank >= 2 ? 1 : 0
  cluster_id         = oci_containerengine_cluster.demo[0].id
  compartment_id     = var.compartment_ocid
  name               = "${var.project_name}-workers"
  kubernetes_version = var.kubernetes_version
  node_shape         = var.node_shape
  freeform_tags      = local.tags
  node_shape_config {
    ocpus         = var.node_ocpus
    memory_in_gbs = var.node_memory_gbs
  }
  node_source_details {
    source_type             = "IMAGE"
    image_id                = lookup(local.image_by_name, var.node_image_name, "")
    boot_volume_size_in_gbs = "50"
  }
  node_config_details {
    size                                = var.node_count
    nsg_ids                             = [module.network.network.nsg_ids.nodes]
    is_pv_encryption_in_transit_enabled = true
    freeform_tags                       = local.tags
    placement_configs {
      availability_domain = data.oci_identity_availability_domains.region[0].availability_domains[0].name
      subnet_id           = module.network.network.subnet_ids.nodes
    }
    node_pool_pod_network_option_details { cni_type = "FLANNEL_OVERLAY" }
  }
  initial_node_labels {
    key   = "workload"
    value = "lasttable"
  }
  lifecycle {
    precondition {
      condition     = contains(keys(local.image_by_name), var.node_image_name)
      error_message = "The pinned OKE image is not available in this region; choose a supported amd64 OKE image for this Kubernetes version."
    }
  }
  timeouts { create = "60m" }
}
resource "oci_artifacts_container_repository" "app" {
  for_each       = local.stage_rank >= 2 ? toset(["backend", "frontend", "notifications"]) : toset([])
  compartment_id = var.compartment_ocid
  display_name   = "${var.project_name}/${each.key}"
  is_public      = false
  freeform_tags  = local.tags
}
output "platform" {
  value = local.stage_rank >= 2 ? {
    cluster_id   = oci_containerengine_cluster.demo[0].id
    node_pool_id = oci_containerengine_node_pool.demo[0].id
    image_id     = lookup(local.image_by_name, var.node_image_name, "")
    repositories = { for k, r in oci_artifacts_container_repository.app : k => r.display_name }
  } : null
}
