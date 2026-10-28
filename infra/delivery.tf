locals {
  smtp_secret_ids = length(var.existing_smtp_secret_ids) > 0 ? var.existing_smtp_secret_ids : { for key, value in oci_vault_secret.smtp : key => value.id }
  app_hostname    = "${var.project_name}.${var.demo_domain}"
  email_sender    = "noreply@${var.demo_domain}"
  database_dsn    = local.stage_rank >= 3 ? one([for p in oci_database_autonomous_database.demo[0].connection_strings[0].profiles : p.value if p.tls_authentication == "SERVER" && p.consumer_group == "LOW" && p.syntax_format == "LONG"]) : ""
  function_secret_ids = local.stage_rank >= 4 ? {
    database-notify = oci_vault_secret.credential["database-notify"].id
    smtp-username   = local.smtp_secret_ids["username"]
    smtp-password   = local.smtp_secret_ids["password"]
  } : {}
}
resource "oci_dns_zone" "demo" {
  count          = local.stage_rank >= 4 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = var.demo_domain
  zone_type      = "PRIMARY"
  scope          = "GLOBAL"
  freeform_tags  = local.tags
}
resource "oci_core_public_ip" "demo" {
  count          = local.stage_rank >= 4 ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.project_name}-https"
  lifetime       = "RESERVED"
  freeform_tags  = local.tags
  # The Kubernetes cloud controller owns assignment to the Load Balancer.
  lifecycle { ignore_changes = [private_ip_id] }
}
resource "oci_dns_rrset" "application" {
  count           = local.stage_rank >= 4 ? 1 : 0
  zone_name_or_id = oci_dns_zone.demo[0].id
  domain          = local.app_hostname
  rtype           = "A"
  items {
    domain = local.app_hostname
    rtype  = "A"
    ttl    = 60
    rdata  = oci_core_public_ip.demo[0].ip_address
  }
}
resource "oci_email_email_domain" "demo" {
  count          = local.stage_rank >= 4 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = var.demo_domain
  description    = "LastTable notification sender domain"
  freeform_tags  = local.tags
}
resource "oci_email_dkim" "demo" {
  count           = local.stage_rank >= 4 ? 1 : 0
  email_domain_id = oci_email_email_domain.demo[0].id
  name            = "${var.project_name}-demo"
  freeform_tags   = local.tags
}
resource "oci_dns_rrset" "dkim" {
  count           = local.stage_rank >= 4 ? 1 : 0
  zone_name_or_id = oci_dns_zone.demo[0].id
  domain          = oci_email_dkim.demo[0].dns_subdomain_name
  rtype           = "CNAME"
  items {
    domain = trimsuffix(oci_email_dkim.demo[0].dns_subdomain_name, ".")
    rtype  = "CNAME"
    ttl    = 60
    rdata  = "${trimsuffix(oci_email_dkim.demo[0].cname_record_value, ".")}."
  }
}
resource "oci_dns_rrset" "spf" {
  count           = local.stage_rank >= 4 ? 1 : 0
  zone_name_or_id = oci_dns_zone.demo[0].id
  domain          = var.demo_domain
  rtype           = "TXT"
  items {
    domain = var.demo_domain
    rtype  = "TXT"
    ttl    = 300
    rdata  = "\"v=spf1 include:eu.rp.oracleemaildelivery.com ~all\""
  }
}
resource "oci_dns_rrset" "dmarc" {
  count           = local.stage_rank >= 4 ? 1 : 0
  zone_name_or_id = oci_dns_zone.demo[0].id
  domain          = "_dmarc.${var.demo_domain}"
  rtype           = "TXT"
  items {
    domain = "_dmarc.${var.demo_domain}"
    rtype  = "TXT"
    ttl    = 300
    rdata  = "\"v=DMARC1; p=none; adkim=r; aspf=r\""
  }
}
resource "oci_email_sender" "demo" {
  count          = local.stage_rank >= 4 ? 1 : 0
  compartment_id = var.compartment_ocid
  email_address  = local.email_sender
  depends_on     = [oci_email_email_domain.demo]
  freeform_tags  = local.tags
}
resource "oci_identity_smtp_credential" "demo" {
  count       = local.stage_rank >= 4 && length(var.existing_smtp_secret_ids) == 0 ? 1 : 0
  user_id     = var.smtp_user_ocid
  description = "${var.project_name} dedicated notification SMTP credential"
  lifecycle {
    precondition {
      condition     = can(regex("^ocid1.user\\.", var.smtp_user_ocid))
      error_message = "Delivery stage requires an existing SMTP-enabled IAM user with a free credential slot."
    }
  }
}
resource "oci_vault_secret" "smtp" {
  for_each       = local.stage_rank >= 4 && length(var.existing_smtp_secret_ids) == 0 ? toset(["username", "password"]) : toset([])
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.demo[0].id
  key_id         = oci_kms_key.secrets[0].id
  secret_name    = "${var.project_name}-smtp-${each.key}"
  freeform_tags  = local.tags
  secret_content {
    content_type = "BASE64"
    content      = base64encode(each.key == "username" ? oci_identity_smtp_credential.demo[0].username : oci_identity_smtp_credential.demo[0].password)
  }
}
resource "random_password" "demo_admin" {
  count   = local.stage_rank >= 4 ? 1 : 0
  length  = 40
  special = false
}
resource "oci_vault_secret" "demo_admin" {
  count          = local.stage_rank >= 4 ? 1 : 0
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.demo[0].id
  key_id         = oci_kms_key.secrets[0].id
  secret_name    = "${var.project_name}-demo-admin"
  freeform_tags  = local.tags
  secret_content {
    content_type = "BASE64"
    content      = base64encode(random_password.demo_admin[0].result)
  }
}
resource "oci_functions_application" "notifications" {
  count                      = local.stage_rank >= 4 ? 1 : 0
  compartment_id             = var.compartment_ocid
  display_name               = "${var.project_name}-notifications"
  shape                      = "GENERIC_X86"
  subnet_ids                 = [module.network.network.subnet_ids.functions]
  network_security_group_ids = [module.network.network.nsg_ids.functions]
  freeform_tags              = local.tags
}
resource "oci_functions_function" "notifications" {
  count          = local.stage_rank >= 4 ? 1 : 0
  application_id = oci_functions_application.notifications[0].id
  display_name   = "booking-status-notification"
  source_details {
    source_type  = "CONTAINER_IMAGE"
    image        = var.notification_image
    image_digest = var.notification_image_digest
  }
  memory_in_mbs      = 512
  timeout_in_seconds = 60
  config = {
    LASTTABLE_CONFIG_JSON = jsonencode({
      region                = var.region
      database_dsn          = local.database_dsn
      credential_secret_ids = local.function_secret_ids
      smtp_host             = "smtp.email.${var.region}.oci.oraclecloud.com"
      smtp_port             = 587
      sender                = local.email_sender
    })
  }
  freeform_tags = local.tags
  lifecycle {
    # OCI DevOps owns later Function image releases; Terraform owns configuration.
    ignore_changes = [source_details[0].image, source_details[0].image_digest]
    precondition {
      condition     = length(var.notification_image) > 0 && can(regex("^sha256:[a-f0-9]{64}$", var.notification_image_digest))
      error_message = "Build, test and push the Function image before enabling the delivery stage."
    }
  }
}
resource "oci_identity_policy" "notification_function" {
  count          = local.stage_rank >= 4 ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.project_name}-function-secrets"
  description    = "Only the notification Function can read its database and SMTP secrets"
  statements     = [for id in values(local.function_secret_ids) : "Allow any-user to read secret-bundles in compartment id ${var.compartment_ocid} where all {request.principal.type = 'fnfunc', request.principal.id = '${oci_functions_function.notifications[0].id}', target.secret.id = '${id}'}"]
  freeform_tags  = local.tags
}
resource "oci_logging_log" "function" {
  count              = local.stage_rank >= 4 ? 1 : 0
  display_name       = "${var.project_name}-function-invocations"
  log_group_id       = oci_logging_log_group.demo[0].id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = 30
  configuration {
    compartment_id = var.compartment_ocid
    source {
      source_type = "OCISERVICE"
      service     = "functions"
      resource    = oci_functions_application.notifications[0].id
      category    = "invoke"
    }
  }
  freeform_tags = local.tags
}
resource "oci_logging_log" "email" {
  for_each           = local.stage_rank >= 4 ? toset(["outboundaccepted", "outboundrelayed"]) : toset([])
  display_name       = "${var.project_name}-email-${each.key}"
  log_group_id       = oci_logging_log_group.demo[0].id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = 30
  configuration {
    compartment_id = var.compartment_ocid
    source {
      source_type = "OCISERVICE"
      service     = "emaildelivery"
      resource    = oci_email_email_domain.demo[0].id
      category    = each.key
    }
  }
  freeform_tags = local.tags
}
output "delivery" {
  value = local.stage_rank >= 4 ? {
    hostname             = local.app_hostname
    sender               = local.email_sender
    zone_id              = oci_dns_zone.demo[0].id
    nameservers          = oci_dns_zone.demo[0].nameservers
    public_ip_id         = oci_core_public_ip.demo[0].id
    public_ip            = oci_core_public_ip.demo[0].ip_address
    email_domain_id      = oci_email_email_domain.demo[0].id
    dkim_id              = oci_email_dkim.demo[0].id
    function_id          = oci_functions_function.notifications[0].id
    function_endpoint    = oci_functions_function.notifications[0].invoke_endpoint
    demo_admin_secret_id = oci_vault_secret.demo_admin[0].id
  } : null
}

resource "oci_dns_rrset" "child_delegation" {
  count           = local.stage_rank >= 4 && var.parent_dns_zone_id != "" ? 1 : 0
  zone_name_or_id = var.parent_dns_zone_id
  domain          = var.demo_domain
  rtype           = "NS"
  dynamic "items" {
    for_each = oci_dns_zone.demo[0].nameservers
    content {
      domain = var.demo_domain
      rtype  = "NS"
      ttl    = 60
      rdata  = "${trimsuffix(items.value.hostname, ".")}."
    }
  }
}
