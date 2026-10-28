locals {
  tags = { project = "LastTable", "managed-by" = "resource-manager", environment = "demo" }
}
module "network" {
  source                   = "./modules/network"
  enable_automation_access = local.stage_rank >= 1
  compartment_ocid         = var.compartment_ocid
  name                     = var.project_name
  vcn_cidr                 = var.vcn_cidr
  tags                     = local.tags
}
