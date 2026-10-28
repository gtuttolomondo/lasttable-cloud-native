terraform {
  required_version = ">= 1.5.7, < 1.6.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 9.11.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "= 0.13.1"
    }
    random = {
      source  = "hashicorp/random"
      version = "= 3.7.2"
    }
  }
}
provider "oci" {
  region = var.region
}
