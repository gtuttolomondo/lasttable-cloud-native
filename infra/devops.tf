variable "initial_backend_image" {
  type        = string
  default     = ""
  description = "Tested OCIR backend digest reference, used as deployment default."
}
variable "initial_frontend_image" {
  type        = string
  default     = ""
  description = "Tested OCIR frontend digest reference, used as deployment default."
}
resource "oci_ons_notification_topic" "devops" {
  count          = local.stage_rank >= 5 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-devops"
  description    = "DevOps lifecycle events; no external subscriptions are created"
  freeform_tags  = local.tags
}
resource "oci_devops_project" "demo" {
  count          = local.stage_rank >= 5 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-delivery"
  description    = "LastTable build, Function release and OKE deployment"
  notification_config { topic_id = oci_ons_notification_topic.devops[0].id }
  freeform_tags = local.tags
}
resource "oci_logging_log" "devops" {
  count              = local.stage_rank >= 5 ? 1 : 0
  display_name       = "${var.project_name}-devops"
  log_group_id       = oci_logging_log_group.demo[0].id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = 30
  configuration {
    compartment_id = var.compartment_ocid
    source {
      source_type = "OCISERVICE"
      service     = "devops"
      resource    = oci_devops_project.demo[0].id
      category    = "all"
    }
  }
  freeform_tags = local.tags
}
resource "oci_devops_repository" "source" {
  count           = local.stage_rank >= 5 ? 1 : 0
  project_id      = oci_devops_project.demo[0].id
  name            = "${var.project_name}-source"
  repository_type = "HOSTED"
  default_branch  = "refs/heads/codex/implementation"
  description     = "Temporary private source before the later public GitHub release"
  freeform_tags   = local.tags
}
resource "oci_devops_deploy_pipeline" "demo" {
  count        = local.stage_rank >= 5 ? 1 : 0
  project_id   = oci_devops_project.demo[0].id
  display_name = "${var.project_name}-release"
  deploy_pipeline_parameters {
    dynamic "items" {
      for_each = { BACKEND_IMAGE = var.initial_backend_image, FRONTEND_IMAGE = var.initial_frontend_image, FUNCTION_IMAGE = var.notification_image, FUNCTION_DIGEST = var.notification_image_digest, RELEASE = "initial" }
      content {
        name          = items.key
        default_value = items.value
      }
    }
  }
  freeform_tags = local.tags
}
resource "oci_devops_build_pipeline" "demo" {
  count        = local.stage_rank >= 5 ? 1 : 0
  project_id   = oci_devops_project.demo[0].id
  display_name = "${var.project_name}-build"
  build_pipeline_parameters {
    dynamic "items" {
      for_each = { REGISTRY_HOST = var.registry_host, REGISTRY_NAMESPACE = data.oci_objectstorage_namespace.demo[0].namespace, COMPARTMENT_OCID = var.compartment_ocid, PROJECT_NAME = var.project_name, RELEASE_LABEL = "v1" }
      content {
        name          = items.key
        default_value = items.value
      }
    }
  }
  freeform_tags = local.tags
}
data "oci_identity_region_subscriptions" "identity" {
  count      = local.stage_rank >= 5 ? 1 : 0
  tenancy_id = var.tenancy_ocid
}
provider "oci" {
  alias  = "identity_home"
  region = local.stage_rank >= 5 ? one([for subscription in data.oci_identity_region_subscriptions.identity[0].region_subscriptions : subscription.region_name if subscription.is_home_region]) : var.region
}
resource "oci_identity_dynamic_group" "pipeline" {
  provider       = oci.identity_home
  for_each       = local.stage_rank >= 5 ? { build = oci_devops_build_pipeline.demo[0].id, deploy = oci_devops_deploy_pipeline.demo[0].id } : {}
  compartment_id = var.tenancy_ocid
  name           = "${var.project_name}-${each.key}-pipeline"
  description    = "Only the exact LastTable ${each.key} pipeline resource"
  matching_rule  = "ALL {resource.id = '${each.value}'}"
  freeform_tags  = local.tags
}
locals {
  build_subject  = local.stage_rank >= 5 ? "dynamic-group id ${oci_identity_dynamic_group.pipeline["build"].id}" : ""
  deploy_subject = local.stage_rank >= 5 ? "dynamic-group id ${oci_identity_dynamic_group.pipeline["deploy"].id}" : ""
}
resource "oci_identity_policy" "build" {
  count          = local.stage_rank >= 5 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-build"
  description    = "Exact managed build pipeline identity in the demo compartment"
  statements = concat(
    [for grant in ["read repos", "manage devops-family", "use subnets", "use vnics", "use network-security-groups", "use ons-topics"] : "Allow ${local.build_subject} to ${grant} in compartment id ${var.compartment_ocid}"],
    ["Allow any-user to manage repos in compartment id ${var.compartment_ocid} where all {any {request.principal.id='${oci_devops_build_pipeline.demo[0].id}', request.user.id='${oci_devops_build_pipeline.demo[0].id}'}, any {request.permission='REPOSITORY_READ', request.permission='REPOSITORY_UPDATE'}}"]
  )
  freeform_tags = local.tags
}
resource "oci_identity_policy" "deploy" {
  count          = local.stage_rank >= 5 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-deploy"
  description    = "Exact deployment pipeline identity for OKE, Function and private networking"
  statements = concat(
    [for grant in ["read all-artifacts", "read repos", "manage cluster", "read fn-app", "use subnets", "use vnics", "use network-security-groups", "use ons-topics"] : "Allow ${local.deploy_subject} to ${grant} in compartment id ${var.compartment_ocid}"],
    ["Allow ${local.deploy_subject} to manage fn-function in compartment id ${var.compartment_ocid} where target.function.id='${oci_functions_function.notifications[0].id}'"]
  )
  freeform_tags = local.tags
}
resource "time_sleep" "devops_identity_settle" {
  count           = local.stage_rank >= 5 ? 1 : 0
  depends_on      = [oci_identity_policy.build, oci_identity_policy.deploy]
  create_duration = "60s"
}
resource "oci_devops_deploy_environment" "oke" {
  count                   = local.stage_rank >= 5 ? 1 : 0
  depends_on              = [oci_identity_policy.deploy]
  project_id              = oci_devops_project.demo[0].id
  display_name            = "${var.project_name}-private-oke"
  deploy_environment_type = "OKE_CLUSTER"
  cluster_id              = oci_containerengine_cluster.demo[0].id
  network_channel {
    network_channel_type = "PRIVATE_ENDPOINT_CHANNEL"
    subnet_id            = module.network.network.subnet_ids.automation
    nsg_ids              = [module.network.network.nsg_ids.automation]
  }
  freeform_tags = local.tags
}
resource "oci_devops_deploy_environment" "function" {
  count                   = local.stage_rank >= 5 ? 1 : 0
  project_id              = oci_devops_project.demo[0].id
  display_name            = "${var.project_name}-notification-function"
  deploy_environment_type = "FUNCTION"
  function_id             = oci_functions_function.notifications[0].id
  freeform_tags           = local.tags
}
resource "oci_devops_deploy_artifact" "manifest" {
  description                = "Application Deployments, Services and TLS PVC; operator identity foundations excluded"
  count                      = local.stage_rank >= 5 ? 1 : 0
  project_id                 = oci_devops_project.demo[0].id
  display_name               = "${var.project_name}-kubernetes-manifests"
  deploy_artifact_type       = "KUBERNETES_MANIFEST"
  argument_substitution_mode = "SUBSTITUTE_PLACEHOLDERS"
  deploy_artifact_source {
    deploy_artifact_source_type = "INLINE"
    # The provider encodes bytes on the wire; supply YAML, avoiding double encoding.
    base64encoded_content = local.application_manifest
  }
  freeform_tags = local.tags
}
resource "oci_devops_deploy_artifact" "function_image" {
  count                      = local.stage_rank >= 5 ? 1 : 0
  project_id                 = oci_devops_project.demo[0].id
  display_name               = "${var.project_name}-function-image"
  deploy_artifact_type       = "DOCKER_IMAGE"
  argument_substitution_mode = "SUBSTITUTE_PLACEHOLDERS"
  deploy_artifact_source {
    deploy_artifact_source_type = "OCIR"
    image_uri                   = "$${FUNCTION_IMAGE}"
    image_digest                = "$${FUNCTION_DIGEST}"
  }
  freeform_tags = local.tags
}
resource "oci_devops_deploy_stage" "function" {
  count                           = local.stage_rank >= 5 ? 1 : 0
  deploy_pipeline_id              = oci_devops_deploy_pipeline.demo[0].id
  display_name                    = "Release notification Function"
  deploy_stage_type               = "DEPLOY_FUNCTION"
  function_deploy_environment_id  = oci_devops_deploy_environment.function[0].id
  docker_image_deploy_artifact_id = oci_devops_deploy_artifact.function_image[0].id
  max_memory_in_mbs               = 512
  function_timeout_in_seconds     = 60
  deploy_stage_predecessor_collection {
    items { id = oci_devops_deploy_pipeline.demo[0].id }
  }
  freeform_tags = local.tags
}
resource "oci_devops_deploy_stage" "oke" {
  count                                   = local.stage_rank >= 5 ? 1 : 0
  deploy_pipeline_id                      = oci_devops_deploy_pipeline.demo[0].id
  display_name                            = "Deploy OKE workloads"
  deploy_stage_type                       = "OKE_DEPLOYMENT"
  oke_cluster_deploy_environment_id       = oci_devops_deploy_environment.oke[0].id
  kubernetes_manifest_deploy_artifact_ids = [oci_devops_deploy_artifact.manifest[0].id]
  namespace                               = "lasttable"
  timeout_in_seconds                      = 900
  rollback_policy { policy_type = "AUTOMATED_STAGE_ROLLBACK_POLICY" }
  deploy_stage_predecessor_collection {
    items { id = oci_devops_deploy_stage.function[0].id }
  }
  freeform_tags = local.tags
}
resource "oci_devops_build_pipeline_stage" "build" {
  description                        = "Build amd64 images and run application contract and Function protocol tests"
  count                              = local.stage_rank >= 5 ? 1 : 0
  depends_on                         = [time_sleep.devops_identity_settle, oci_logging_log.devops]
  build_pipeline_id                  = oci_devops_build_pipeline.demo[0].id
  display_name                       = "Build and test release images"
  build_pipeline_stage_type          = "BUILD"
  image                              = "OL8_X86_64_STANDARD_10"
  stage_execution_timeout_in_seconds = 3600
  build_spec_file                    = "ci/build_spec.yaml"
  primary_build_source               = "source"
  build_runner_shape_config {
    build_runner_type = "CUSTOM"
    ocpus             = 2
    memory_in_gbs     = 16
  }
  build_source_collection {
    items {
      connection_type = "DEVOPS_CODE_REPOSITORY"
      name            = "source"
      branch          = "codex/implementation"
      repository_id   = oci_devops_repository.source[0].id
      repository_url  = oci_devops_repository.source[0].http_url
    }
  }
  private_access_config {
    network_channel_type = "SERVICE_VNIC_CHANNEL"
    subnet_id            = module.network.network.subnet_ids.automation
    nsg_ids              = [module.network.network.nsg_ids.automation]
  }
  build_pipeline_stage_predecessor_collection {
    items { id = oci_devops_build_pipeline.demo[0].id }
  }
  freeform_tags = local.tags
}
resource "oci_devops_build_pipeline_stage" "trigger" {
  description                    = "Deploy only the release whose OCIR digests have been verified"
  count                          = local.stage_rank >= 5 ? 1 : 0
  build_pipeline_id              = oci_devops_build_pipeline.demo[0].id
  display_name                   = "Deploy verified release"
  build_pipeline_stage_type      = "TRIGGER_DEPLOYMENT_PIPELINE"
  deploy_pipeline_id             = oci_devops_deploy_pipeline.demo[0].id
  is_pass_all_parameters_enabled = true
  build_pipeline_stage_predecessor_collection {
    items { id = oci_devops_build_pipeline_stage.resolve[0].id }
  }
  freeform_tags = local.tags
}
output "devops" {
  value = local.stage_rank >= 5 ? {
    project_id          = oci_devops_project.demo[0].id
    repository_id       = oci_devops_repository.source[0].id
    repository_ssh_url  = oci_devops_repository.source[0].ssh_url
    repository_http_url = oci_devops_repository.source[0].http_url
    build_pipeline_id   = oci_devops_build_pipeline.demo[0].id
    deploy_pipeline_id  = oci_devops_deploy_pipeline.demo[0].id
  } : null
}

resource "oci_devops_deploy_artifact" "delivery" {
  for_each                   = local.stage_rank >= 5 ? toset(["backend", "frontend", "notifications"]) : toset([])
  project_id                 = oci_devops_project.demo[0].id
  display_name               = "${var.project_name}-${each.key}-delivery"
  deploy_artifact_type       = "DOCKER_IMAGE"
  argument_substitution_mode = "SUBSTITUTE_PLACEHOLDERS"
  deploy_artifact_source {
    deploy_artifact_source_type = "OCIR"
    image_uri                   = "${var.registry_host}/${data.oci_objectstorage_namespace.demo[0].namespace}/${var.project_name}/${each.key}:$${RELEASE}"
  }
  freeform_tags = local.tags
}
resource "oci_devops_build_pipeline_stage" "deliver" {
  description               = "Publish the three tested build outputs using native OCI artifact delivery"
  count                     = local.stage_rank >= 5 ? 1 : 0
  build_pipeline_id         = oci_devops_build_pipeline.demo[0].id
  display_name              = "Deliver tested images to private OCIR"
  build_pipeline_stage_type = "DELIVER_ARTIFACT"
  deliver_artifact_collection {
    dynamic "items" {
      for_each = oci_devops_deploy_artifact.delivery
      content {
        artifact_id   = items.value.id
        artifact_name = "${items.key}-image"
      }
    }
  }
  build_pipeline_stage_predecessor_collection {
    items { id = oci_devops_build_pipeline_stage.build[0].id }
  }
  freeform_tags = local.tags
}
resource "oci_devops_build_pipeline_stage" "resolve" {
  description                        = "Read authoritative image digests from OCIR before triggering deployment"
  count                              = local.stage_rank >= 5 ? 1 : 0
  build_pipeline_id                  = oci_devops_build_pipeline.demo[0].id
  display_name                       = "Resolve authoritative OCIR digests"
  build_pipeline_stage_type          = "BUILD"
  image                              = "OL8_X86_64_STANDARD_10"
  stage_execution_timeout_in_seconds = 600
  build_spec_file                    = "ci/resolve_spec.yaml"
  primary_build_source               = "source"
  build_runner_shape_config { build_runner_type = "DEFAULT" }
  build_source_collection {
    items {
      connection_type = "DEVOPS_CODE_REPOSITORY"
      name            = "source"
      branch          = "codex/implementation"
      repository_id   = oci_devops_repository.source[0].id
      repository_url  = oci_devops_repository.source[0].http_url
    }
  }
  build_pipeline_stage_predecessor_collection {
    items { id = oci_devops_build_pipeline_stage.deliver[0].id }
  }
  freeform_tags = local.tags
}
