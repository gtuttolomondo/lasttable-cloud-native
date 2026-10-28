variable "compartment_ocid" { type = string }
variable "name" { type = string }
variable "vcn_cidr" { type = string }
variable "tags" { type = map(string) }
variable "enable_automation_access" {
  type    = bool
  default = false
}

locals {
  subnets = {
    ingress    = { cidr = cidrsubnet(var.vcn_cidr, 8, 0), public = true }
    api        = { cidr = cidrsubnet(var.vcn_cidr, 8, 1), public = false }
    nodes      = { cidr = cidrsubnet(var.vcn_cidr, 4, 1), public = false }
    database   = { cidr = cidrsubnet(var.vcn_cidr, 8, 32), public = false }
    kafka      = { cidr = cidrsubnet(var.vcn_cidr, 8, 33), public = false }
    functions  = { cidr = cidrsubnet(var.vcn_cidr, 8, 34), public = false }
    automation = { cidr = cidrsubnet(var.vcn_cidr, 8, 35), public = false }
  }
}
resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.name}-vcn"
  dns_label      = var.name
  freeform_tags  = var.tags
}
data "oci_core_services" "regional" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}
resource "oci_core_internet_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-igw"
  freeform_tags  = var.tags
}
resource "oci_core_nat_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-nat"
  freeform_tags  = var.tags
}
resource "oci_core_service_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-services"
  freeform_tags  = var.tags
  services { service_id = one(data.oci_core_services.regional.services).id }
}
resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-public"
  freeform_tags  = var.tags
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.this.id
  }
}
resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-private"
  freeform_tags  = var.tags
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_nat_gateway.this.id
  }
  route_rules {
    destination       = one(data.oci_core_services.regional.services).cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.this.id
  }
}
# Security lists and NSGs are additive. The baseline deliberately grants no TCP ingress.
resource "oci_core_security_list" "baseline" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-baseline"
  freeform_tags  = var.tags
  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "6"
    tcp_options {
      min = 443
      max = 443
    }
    description = "TLS to OCI APIs, registries and package repositories through NAT or service gateway"
  }
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "1"
    icmp_options {
      type = 3
      code = 4
    }
    description = "Path MTU discovery"
  }
}
# Kafka broker VNICs are service-managed; access is enforced by this subnet-specific SL.
resource "oci_core_security_list" "kafka" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-kafka"
  freeform_tags  = var.tags
  dynamic "ingress_security_rules" {
    for_each = toset([local.subnets.nodes.cidr, local.subnets.automation.cidr])
    content {
      source   = ingress_security_rules.value
      protocol = "6"
      tcp_options {
        min = 9092
        max = 9092
      }
      description = "Authenticated SASL_SSL Kafka clients"
    }
  }
  ingress_security_rules {
    source      = local.subnets.kafka.cidr
    protocol    = "all"
    description = "Service-managed broker coordination within dedicated subnet"
  }
  egress_security_rules {
    destination = local.subnets.kafka.cidr
    protocol    = "all"
  }
}
resource "oci_core_subnet" "this" {
  for_each                   = local.subnets
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  display_name               = "${var.name}-${each.key}"
  cidr_block                 = each.value.cidr
  dns_label                  = each.key
  prohibit_public_ip_on_vnic = !each.value.public
  prohibit_internet_ingress  = !each.value.public
  route_table_id             = each.value.public ? oci_core_route_table.public.id : oci_core_route_table.private.id
  security_list_ids          = concat([oci_core_security_list.baseline.id], each.key == "kafka" ? [oci_core_security_list.kafka.id] : [], each.key == "automation" && var.enable_automation_access ? [oci_core_security_list.automation[0].id] : [])
  freeform_tags              = var.tags
}
resource "oci_core_network_security_group" "role" {
  for_each       = toset(["ingress", "api", "nodes", "database", "functions", "automation"])
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-${each.key}"
  freeform_tags  = var.tags
}
locals {
  tcp_rules = {
    https           = { role = "ingress", direction = "INGRESS", peer = "0.0.0.0/0", min = 443, max = 443 }
    http            = { role = "ingress", direction = "INGRESS", peer = "0.0.0.0/0", min = 80, max = 80 }
    lb_nodes_out    = { role = "ingress", direction = "EGRESS", peer = local.subnets.nodes.cidr, min = 30000, max = 32767 }
    lb_health_out   = { role = "ingress", direction = "EGRESS", peer = local.subnets.nodes.cidr, min = 10256, max = 10256 }
    lb_nodes_in     = { role = "nodes", direction = "INGRESS", peer = local.subnets.ingress.cidr, min = 30000, max = 32767 }
    lb_health_in    = { role = "nodes", direction = "INGRESS", peer = local.subnets.ingress.cidr, min = 10256, max = 10256 }
    api_nodes_in    = { role = "api", direction = "INGRESS", peer = local.subnets.nodes.cidr, min = 6443, max = 6443 }
    api_jobs_in     = { role = "api", direction = "INGRESS", peer = local.subnets.automation.cidr, min = 6443, max = 6443 }
    api_control     = { role = "api", direction = "INGRESS", peer = local.subnets.nodes.cidr, min = 12250, max = 12250 }
    api_kubelet_out = { role = "api", direction = "EGRESS", peer = local.subnets.nodes.cidr, min = 10250, max = 10250 }
    kubelet_in      = { role = "nodes", direction = "INGRESS", peer = local.subnets.api.cidr, min = 10250, max = 10250 }
    node_api_out    = { role = "nodes", direction = "EGRESS", peer = local.subnets.api.cidr, min = 6443, max = 6443 }
    node_ctl_out    = { role = "nodes", direction = "EGRESS", peer = local.subnets.api.cidr, min = 12250, max = 12250 }
    jobs_api_out    = { role = "automation", direction = "EGRESS", peer = local.subnets.api.cidr, min = 6443, max = 6443 }
    db_nodes_in     = { role = "database", direction = "INGRESS", peer = local.subnets.nodes.cidr, min = 1521, max = 1522 }
    db_fn_in        = { role = "database", direction = "INGRESS", peer = local.subnets.functions.cidr, min = 1521, max = 1522 }
    db_jobs_in      = { role = "database", direction = "INGRESS", peer = local.subnets.automation.cidr, min = 1521, max = 1522 }
    nodes_db_out    = { role = "nodes", direction = "EGRESS", peer = local.subnets.database.cidr, min = 1521, max = 1522 }
    fn_db_out       = { role = "functions", direction = "EGRESS", peer = local.subnets.database.cidr, min = 1521, max = 1522 }
    jobs_db_out     = { role = "automation", direction = "EGRESS", peer = local.subnets.database.cidr, min = 1521, max = 1522 }
    kafka_nodes     = { role = "nodes", direction = "EGRESS", peer = local.subnets.kafka.cidr, min = 9092, max = 9092 }
    kafka_jobs      = { role = "automation", direction = "EGRESS", peer = local.subnets.kafka.cidr, min = 9092, max = 9092 }
    smtp_out        = { role = "functions", direction = "EGRESS", peer = "0.0.0.0/0", min = 587, max = 587 }
  }
}
resource "oci_core_network_security_group_security_rule" "tcp" {
  for_each                  = local.tcp_rules
  network_security_group_id = oci_core_network_security_group.role[each.value.role].id
  direction                 = each.value.direction
  protocol                  = "6"
  source                    = each.value.direction == "INGRESS" ? each.value.peer : null
  destination               = each.value.direction == "EGRESS" ? each.value.peer : null
  source_type               = each.value.direction == "INGRESS" ? "CIDR_BLOCK" : null
  destination_type          = each.value.direction == "EGRESS" ? "CIDR_BLOCK" : null
  description               = each.key
  tcp_options {
    destination_port_range {
      min = each.value.min
      max = each.value.max
    }
  }
}
resource "oci_core_network_security_group_security_rule" "node_mesh" {
  for_each                  = toset(["INGRESS", "EGRESS"])
  network_security_group_id = oci_core_network_security_group.role["nodes"].id
  direction                 = each.value
  protocol                  = "all"
  source                    = each.value == "INGRESS" ? local.subnets.nodes.cidr : null
  destination               = each.value == "EGRESS" ? local.subnets.nodes.cidr : null
  source_type               = each.value == "INGRESS" ? "CIDR_BLOCK" : null
  destination_type          = each.value == "EGRESS" ? "CIDR_BLOCK" : null
  description               = "OKE node and overlay mesh within dedicated node subnet"
}
output "network" {
  value = {
    vcn_id       = oci_core_vcn.this.id
    cidr         = var.vcn_cidr
    subnet_ids   = { for k, v in oci_core_subnet.this : k => v.id }
    subnet_cidrs = { for k, v in local.subnets : k => v.cidr }
    nsg_ids      = { for k, v in oci_core_network_security_group.role : k => v.id }
  }
}

# Managed Bastion private endpoints do not attach the custom automation NSG.
resource "oci_core_security_list" "automation" {
  count          = var.enable_automation_access ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-automation"
  freeform_tags  = var.tags
  dynamic "egress_security_rules" {
    for_each = {
      api   = { cidr = local.subnets.api.cidr, port = 6443 }
      db    = { cidr = local.subnets.database.cidr, port = 1521 }
      kafka = { cidr = local.subnets.kafka.cidr, port = 9092 }
    }
    content {
      destination = egress_security_rules.value.cidr
      protocol    = "6"
      tcp_options {
        min = egress_security_rules.value.port
        max = egress_security_rules.value.port
      }
    }
  }
}
