# ---------------------------------------------------------------------------
# Konnect control plane for this environment
# ---------------------------------------------------------------------------
resource "konnect_gateway_control_plane" "this" {
  name         = var.control_plane_name
  description  = "ShopGlobal Smart Routing AI Gateway - ${var.env} (managed by Terraform)"
  cluster_type = "CLUSTER_TYPE_CONTROL_PLANE"
  auth_type    = "pinned_client_certs"
  labels = {
    project    = "shopglobal"
    env        = var.env
    managed_by = "terraform"
  }
}

locals {
  cp_host = trimprefix(konnect_gateway_control_plane.this.config.control_plane_endpoint, "https://")
  tp_host = trimprefix(konnect_gateway_control_plane.this.config.telemetry_endpoint, "https://")
}

# ---------------------------------------------------------------------------
# Data plane client certificate: generated here, pinned in Konnect, mounted in Kubernetes.
# Nobody copies or pastes certificates any more (lab lesson: a pasted key leaked into chat).
# The private key lives in Terraform state -> keep state on an encrypted/remote backend in production.
# ---------------------------------------------------------------------------
resource "tls_private_key" "dp" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P384"
}

resource "tls_self_signed_cert" "dp" {
  private_key_pem       = tls_private_key.dp.private_key_pem
  validity_period_hours = 24 * 365 * 3
  early_renewal_hours   = 24 * 30 # re-issue 30 days before expiry on the next apply
  allowed_uses          = ["digital_signature", "key_encipherment", "client_auth", "server_auth"]
  subject {
    common_name = "kong_clustering"
  }
}

resource "konnect_gateway_data_plane_client_certificate" "dp" {
  control_plane_id = konnect_gateway_control_plane.this.id
  cert             = tls_self_signed_cert.dp.cert_pem
  title            = "shopglobal-${var.env}-k8s"
}

# ---------------------------------------------------------------------------
# Optional: least-privilege identity for CI/CD (instead of a personal token)
# ---------------------------------------------------------------------------
resource "konnect_system_account" "ci" {
  count           = var.create_ci_system_account ? 1 : 0
  name            = "shopglobal-ci-${var.env}"
  description     = "GitHub Actions deployer for ${var.control_plane_name}"
  konnect_managed = false
}

resource "konnect_system_account_role" "ci_cp_admin" {
  count            = var.create_ci_system_account ? 1 : 0
  account_id       = konnect_system_account.ci[0].id
  entity_id        = konnect_gateway_control_plane.this.id
  entity_region    = var.konnect_region
  entity_type_name = "Control Planes"
  role_name        = "Admin"
}

resource "konnect_system_account_access_token" "ci" {
  count      = var.create_ci_system_account ? 1 : 0
  account_id = konnect_system_account.ci[0].id
  name       = "github-actions"
  expires_at = var.ci_token_expires_at
}
