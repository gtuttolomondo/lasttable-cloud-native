variable "registry_host" {
  type        = string
  default     = "lin.ocir.io"
  description = "OCIR endpoint for the selected region."
}
variable "registry_helper_image" {
  type        = string
  default     = "ghcr.io/oracle/oci-cli@sha256:1f66c3579ce772f02dbfed1232b455561f341391b80f28e875ec38b462880e4b"
  description = "Verified official Oracle CLI amd64 image used to recover expired private pull credentials."
}
data "oci_objectstorage_namespace" "demo" {
  count          = local.stage_rank >= 5 ? 1 : 0
  compartment_id = var.compartment_ocid
}
locals {
  runtime_config = local.stage_rank >= 5 ? {
    region                  = var.region
    database_dsn            = local.database_dsn
    kafka_bootstrap_servers = join(",", [for u in data.oci_managed_kafka_kafka_cluster.ready[0].kafka_bootstrap_urls : u.url if strcontains(upper(u.name), "SASL")])
    credential_secret_ids   = { for k, v in local.application_secret_ids : k => v if !contains(["database-admin", "database-owner", "database-notify"], k) }
    function_id             = oci_functions_function.notifications[0].id
    function_endpoint       = oci_functions_function.notifications[0].invoke_endpoint
  } : null
  app_roles          = toset(["api", "publisher", "confirmation", "trending", "notifications", "observer", "replay"])
  pod_security       = { runAsNonRoot = true, runAsUser = 65532, fsGroup = 65532, seccompProfile = { type = "RuntimeDefault" } }
  container_security = { allowPrivilegeEscalation = false, capabilities = { drop = ["ALL"] } }
  common_labels      = { "app.kubernetes.io/part-of" = "lasttable" }
  app_deployments = [for role in local.app_roles : {
    apiVersion = "apps/v1", kind = "Deployment",
    metadata   = { name = role, namespace = "lasttable", labels = local.common_labels },
    spec = { replicas = 1, revisionHistoryLimit = 3, progressDeadlineSeconds = 300,
      strategy = jsondecode(role == "api" ? jsonencode({ type = "RollingUpdate", rollingUpdate = { maxSurge = 1, maxUnavailable = 0 } }) : jsonencode({ type = "Recreate" })), selector = { matchLabels = { app = role } },
      template = { metadata = { labels = { app = role }, annotations = { "lasttable/release" = "$${RELEASE}" } }, spec = merge({
        serviceAccountName            = role, imagePullSecrets = [{ name = "ocir-pull" }], securityContext = local.pod_security,
        terminationGracePeriodSeconds = 45,
        containers = [merge({ name = role, image = "$${BACKEND_IMAGE}", imagePullPolicy = "IfNotPresent",
          env             = concat([{ name = "LASTTABLE_RELEASE", value = "$${RELEASE}" }], role == "api" ? [{ name = "LASTTABLE_TABLE_ADMIN_CODE", valueFrom = { secretKeyRef = { name = var.table_admin_secret_name, key = "code" } } }] : []),
          resources       = { requests = { cpu = "100m", memory = "192Mi" }, limits = { cpu = "1000m", memory = "512Mi" } },
          securityContext = merge(local.container_security, { readOnlyRootFilesystem = true }),
          volumeMounts    = [{ name = "config", mountPath = "/config", readOnly = true }, { name = "tmp", mountPath = "/tmp" }],
          }, jsondecode(role == "api" ? jsonencode({
            command        = ["uvicorn", "lasttable.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"],
            ports          = [{ containerPort = 8000 }],
            readinessProbe = { httpGet = { path = "/api/ready", port = 8000 }, initialDelaySeconds = 5, periodSeconds = 5, timeoutSeconds = 5 },
            livenessProbe  = { httpGet = { path = "/api/health", port = 8000 }, initialDelaySeconds = 30, periodSeconds = 15, timeoutSeconds = 3 }
        }) : jsonencode({ command = ["python", "-m", "lasttable.worker", role] })))],
        volumes = [{ name = "config", configMap = { name = "application-config" } }, { name = "tmp", emptyDir = {} }]
      }) }
    }
  }]
  registry_pod = {
    restartPolicy = "Never", serviceAccountName = "registry", securityContext = local.pod_security,
    containers = [{ name = "refresh", image = var.registry_helper_image, command = ["python3", "/scripts/refresh_registry.py"],
      env          = [{ name = "REGISTRY_HOST", value = var.registry_host }],
      volumeMounts = [{ name = "script", mountPath = "/scripts", readOnly = true }],
      resources    = { requests = { cpu = "50m", memory = "128Mi" }, limits = { cpu = "500m", memory = "384Mi" } },
    securityContext = local.container_security }],
    volumes = [{ name = "script", configMap = { name = "registry-helper" } }]
  }
  # Identity, registry credentials and deployment RBAC are operator foundations.
  # DevOps application releases contain only Deployments, Services and the TLS PVC.
  foundation_objects = jsondecode(local.stage_rank >= 5 ? jsonencode(concat(
    [{ apiVersion = "v1", kind = "Namespace", metadata = { name = "lasttable", labels = local.common_labels } }],
    [{ apiVersion = "storage.k8s.io/v1", kind = "StorageClass", metadata = { name = "${var.project_name}-encrypted-bv" }, provisioner = "blockvolume.csi.oraclecloud.com", parameters = { "attachment-type" = "paravirtualized" }, reclaimPolicy = "Delete", volumeBindingMode = "WaitForFirstConsumer", allowVolumeExpansion = true }],
    [for role in setunion(local.app_roles, toset(["registry", "frontend"])) : { apiVersion = "v1", kind = "ServiceAccount", metadata = { name = role, namespace = "lasttable", labels = local.common_labels } }],
    [
      { apiVersion = "v1", kind = "ConfigMap", metadata = { name = "application-config", namespace = "lasttable" }, data = { "config.json" = jsonencode(local.runtime_config) } },
      { apiVersion = "v1", kind = "ConfigMap", metadata = { name = "registry-helper", namespace = "lasttable" }, data = { "refresh_registry.py" = file("${path.module}/assets/refresh_registry.py") } },
      { apiVersion = "rbac.authorization.k8s.io/v1", kind = "Role", metadata = { name = "registry-refresh", namespace = "lasttable" }, rules = [{ apiGroups = [""], resources = ["secrets"], resourceNames = ["ocir-pull"], verbs = ["get", "patch"] }] },
      { apiVersion = "rbac.authorization.k8s.io/v1", kind = "RoleBinding", metadata = { name = "registry-refresh", namespace = "lasttable" }, roleRef = { apiGroup = "rbac.authorization.k8s.io", kind = "Role", name = "registry-refresh" }, subjects = [{ kind = "ServiceAccount", name = "registry", namespace = "lasttable" }] },
      { apiVersion = "batch/v1", kind = "CronJob", metadata = { name = "registry-refresh", namespace = "lasttable" }, spec = { schedule = "*/20 * * * *", concurrencyPolicy = "Forbid", successfulJobsHistoryLimit = 1, failedJobsHistoryLimit = 2, jobTemplate = { spec = { backoffLimit = 2, activeDeadlineSeconds = 120, template = { spec = local.registry_pod } } } } },
      { apiVersion = "rbac.authorization.k8s.io/v1", kind = "Role", metadata = { name = "application-deployer", namespace = "lasttable" }, rules = [
        { apiGroups = ["apps"], resources = ["deployments"], verbs = ["get", "list", "watch", "create", "update", "patch"] },
        { apiGroups = [""], resources = ["services", "persistentvolumeclaims"], verbs = ["get", "list", "watch", "create", "update", "patch"] },
        { apiGroups = [""], resources = ["pods", "pods/log", "events"], verbs = ["get", "list", "watch"] },
        { apiGroups = ["apps"], resources = ["replicasets"], verbs = ["get", "list", "watch"] },
        { apiGroups = ["apps"], resources = ["deployments"], resourceNames = sort(tolist(setunion(local.app_roles, toset(["frontend"])))), verbs = ["delete"] },
        { apiGroups = [""], resources = ["services"], resourceNames = ["backend", "frontend"], verbs = ["delete"] },
        { apiGroups = [""], resources = ["persistentvolumeclaims"], resourceNames = ["https-certificates"], verbs = ["delete"] }
      ] },
      { apiVersion = "rbac.authorization.k8s.io/v1", kind = "RoleBinding", metadata = { name = "application-deployer", namespace = "lasttable" }, roleRef = { apiGroup = "rbac.authorization.k8s.io", kind = "Role", name = "application-deployer" }, subjects = [{ apiGroup = "rbac.authorization.k8s.io", kind = "User", name = oci_devops_deploy_pipeline.demo[0].id }] },
    ]
  )) : "[]")
  application_objects = jsondecode(local.stage_rank >= 5 ? jsonencode(concat(
    [
      { apiVersion = "v1", kind = "Service", metadata = { name = "backend", namespace = "lasttable" }, spec = { selector = { app = "api" }, ports = [{ port = 8000, targetPort = 8000 }] } },
      { apiVersion = "v1", kind = "PersistentVolumeClaim", metadata = { name = "https-certificates", namespace = "lasttable" }, spec = { accessModes = ["ReadWriteOnce"], storageClassName = "${var.project_name}-encrypted-bv", resources = { requests = { storage = "50Gi" } } } },
      { apiVersion = "apps/v1", kind = "Deployment", metadata = { name = "frontend", namespace = "lasttable", labels = local.common_labels },
        spec = { replicas = 1, revisionHistoryLimit = 3, progressDeadlineSeconds = 600, strategy = { type = "Recreate" }, selector = { matchLabels = { app = "frontend" } },
          template = { metadata = { labels = { app = "frontend" }, annotations = { "lasttable/release" = "$${RELEASE}" } }, spec = { serviceAccountName = "frontend", automountServiceAccountToken = false,
            imagePullSecrets = [{ name = "ocir-pull" }], securityContext = local.pod_security,
            containers = [{ name = "frontend", image = "$${FRONTEND_IMAGE}", imagePullPolicy = "IfNotPresent",
              env            = [{ name = "LASTTABLE_SITE", value = local.app_hostname }, { name = "LASTTABLE_API", value = "backend:8000" }],
              ports          = [{ containerPort = 8080 }, { containerPort = 8443 }],
              resources      = { requests = { cpu = "50m", memory = "64Mi" }, limits = { cpu = "500m", memory = "256Mi" } }, securityContext = merge(local.container_security, { readOnlyRootFilesystem = true }),
              readinessProbe = { tcpSocket = { port = 8080 }, initialDelaySeconds = 3, periodSeconds = 5 },
              livenessProbe  = { tcpSocket = { port = 8080 }, initialDelaySeconds = 20, periodSeconds = 15 },
            volumeMounts = [{ name = "certificates", mountPath = "/data" }, { name = "config", mountPath = "/config" }, { name = "tmp", mountPath = "/tmp" }] }],
      volumes = [{ name = "certificates", persistentVolumeClaim = { claimName = "https-certificates" } }, { name = "config", emptyDir = {} }, { name = "tmp", emptyDir = {} }] } } } },
      { apiVersion = "v1", kind = "Service", metadata = { name = "frontend", namespace = "lasttable", labels = local.common_labels, annotations = {
        "oci.oraclecloud.com/load-balancer-type"                      = "lb",
        "oci.oraclecloud.com/reserved-ips"                            = oci_core_public_ip.demo[0].ip_address,
        "service.beta.kubernetes.io/oci-load-balancer-shape"          = "flexible",
        "service.beta.kubernetes.io/oci-load-balancer-shape-flex-min" = "10",
        "service.beta.kubernetes.io/oci-load-balancer-shape-flex-max" = "100",
        "service.beta.kubernetes.io/oci-load-balancer-subnet1"        = module.network.network.subnet_ids.ingress,
        "oci.oraclecloud.com/oci-network-security-groups"             = module.network.network.nsg_ids.ingress,
        "oci.oraclecloud.com/security-rule-management-mode"           = "None"
      } }, spec = { type = "LoadBalancer", selector = { app = "frontend" }, ports = [{ name = "http", port = 80, targetPort = 8080, protocol = "TCP" }, { name = "https", port = 443, targetPort = 8443, protocol = "TCP" }] } }
    ], local.app_deployments
  )) : "[]")
  application_manifest = join("\n---\n", [for object in local.application_objects : yamlencode(object)])
}

output "kubernetes_foundation" {
  description = "Operator-applied namespace, identities and narrowly scoped release permissions; no plaintext credentials."
  value       = local.stage_rank >= 5 ? yamlencode({ apiVersion = "v1", kind = "List", items = local.foundation_objects }) : null
  sensitive   = true
}
