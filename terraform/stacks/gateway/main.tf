# ---------------------------------------------------------------------------
# Gateway stack: everything that lives INSIDE the control plane.
#   gateway.generated.tf  services, routes, AI plugins  (built from OpenAPI by scripts/build-gateway.sh)
#   consumers.tf          consumers + credentials        (hand-written: keys come from secrets)
# The control plane itself is owned by the platform stack; we read its ID from that state.
# ---------------------------------------------------------------------------
terraform {
  required_version = ">= 1.6"
  required_providers {
    konnect = {
      source  = "kong/konnect"
      version = "~> 3.28"
    }
  }
  backend "local" {} # path passed by scripts/tf.sh (-backend-config=path=...)
}

provider "konnect" {
  server_url = var.konnect_server_url
  # token from env KONNECT_TOKEN (personal access token) or KONNECT_SPAT (system account)
}

data "terraform_remote_state" "platform" {
  backend = "local"
  config  = { path = pathexpand("${var.state_dir}/platform-${var.env}.tfstate") }
}

locals {
  control_plane_id = data.terraform_remote_state.platform.outputs.control_plane_id
}
