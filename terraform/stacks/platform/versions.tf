# PLATFORM stack (one state per environment): everything the gateway runs ON.
#   Konnect: control plane, data-plane client certificate, optional CI system account
#   Kubernetes: namespace, secrets, Kong data planes (Helm), Redis Stack, mock APIs, bot
#
# Run via:  bash scripts/tf.sh platform <staging|prod> <plan|apply>
terraform {
  required_version = ">= 1.6"
  required_providers {
    konnect    = { source = "kong/konnect", version = "~> 3.28" }
    tls        = { source = "hashicorp/tls", version = "~> 4.1" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.38" }
    helm       = { source = "hashicorp/helm", version = "~> 3.0" }
  }
  backend "local" {} # path supplied by scripts/tf.sh (-backend-config)
}

# Auth: KONNECT_TOKEN (personal token, bootstrap) or KONNECT_SPAT (system account token, CI)
provider "konnect" {
  server_url = var.konnect_server_url
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig)
  config_context = var.kube_context
}

provider "helm" {
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig)
    config_context = var.kube_context
  }
}
