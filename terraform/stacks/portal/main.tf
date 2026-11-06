# ---------------------------------------------------------------------------
# Portal stack: the same OpenAPI specs that BUILD the gateway are PUBLISHED as API docs.
# One source of truth -> the docs can never drift from what the gateway enforces.
# ---------------------------------------------------------------------------
terraform {
  required_version = ">= 1.6"
  required_providers {
    konnect = {
      source  = "kong/konnect"
      version = "~> 3.28"
    }
  }
  backend "local" {}
}

provider "konnect" {
  server_url = var.konnect_server_url
}

data "terraform_remote_state" "gateway" {
  backend = "local"
  config  = { path = pathexpand("${var.state_dir}/gateway-${var.env}.tfstate") }
}

locals {
  specs = "${path.module}/../../../apiops/openapi"
  apis = {
    orders-api = {
      file        = "orders-api.yaml"
      description = "Order status for the shopping assistant (PII removed at the gateway)"
    }
    catalog-api = {
      file        = "catalog-api.yaml"
      description = "Product catalog lookups"
    }
    shop-assistant = {
      file        = "shop-assistant-api.yaml"
      description = "Smart-routed AI assistant (English/Spanish, cost-aware model routing)"
    }
  }
}

resource "konnect_portal" "this" {
  name                   = "shopglobal-${var.env}"
  display_name           = "ShopGlobal APIs (${var.env})"
  description            = "Developer portal for the ShopGlobal shopping assistant - managed by Terraform"
  authentication_enabled = true
  default_api_visibility = "private"
  force_destroy          = var.env == "staging" ? "true" : "false"
  labels                 = { project = "shopglobal", env = var.env }
}

resource "konnect_api" "this" {
  for_each     = local.apis
  name         = "${each.key}-${var.env}"
  description  = each.value.description
  version      = yamldecode(file("${local.specs}/${each.value.file}")).info.version
  spec_content = file("${local.specs}/${each.value.file}")
  labels       = { project = "shopglobal", env = var.env }
}

resource "konnect_api_publication" "this" {
  for_each   = konnect_api.this
  api_id     = each.value.id
  portal_id  = konnect_portal.this.id
  visibility = "private" # only signed-in developers
}

# Links the catalog entry to the gateway service that actually serves it
resource "konnect_api_implementation" "this" {
  for_each = konnect_api.this
  api_id   = each.value.id
  service_reference = {
    service = {
      control_plane_id = data.terraform_remote_state.gateway.outputs.control_plane_id
      id               = data.terraform_remote_state.gateway.outputs.service_ids[each.key]
    }
  }
}

output "portal_id" {
  value = konnect_portal.this.id
}
