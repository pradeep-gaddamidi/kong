# ---------------------------------------------------------------------------
# Kubernetes: one namespace per environment. Every component of the environment runs here,
# so short service names (redis-stack, mock-order-api) in the gateway config just work.
# ---------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "env" {
  metadata {
    name   = "shopglobal-${var.env}"
    labels = { project = "shopglobal", env = var.env }
  }
}

locals {
  ns          = kubernetes_namespace_v1.env.metadata[0].name
  kong_name   = "kong-dp" # Helm fullnameOverride -> services kong-dp-proxy, ...
  kong_proxy  = "kong-dp-proxy"
  secret_name = "shopglobal-secrets"
}

# Cluster certificate for the Konnect data plane (from konnect.tf)
resource "kubernetes_secret_v1" "kong_cluster_cert" {
  metadata {
    name      = "kong-cluster-cert"
    namespace = local.ns
  }
  type = "kubernetes.io/tls"
  data = {
    "tls.crt" = tls_self_signed_cert.dp.cert_pem
    "tls.key" = tls_private_key.dp.private_key_pem
  }
}

# Runtime secrets. Kong reads the OpenAI header through the env vault:
#   plugin config: header_value: "{vault://env/kong-openai-auth-header}"
#   container env: KONG_OPENAI_AUTH_HEADER = "Bearer sk-..."
resource "kubernetes_secret_v1" "runtime" {
  metadata {
    name      = local.secret_name
    namespace = local.ns
  }
  data = {
    "kong-openai-auth-header" = "Bearer ${var.openai_api_key}"
    "bot-api-key"             = var.bot_api_key
  }
}

# ---------------------------------------------------------------------------
# Kong data planes (official kong/kong chart, Konnect hybrid mode)
# ---------------------------------------------------------------------------
resource "helm_release" "kong_dp" {
  name             = local.kong_name
  namespace        = local.ns
  repository       = "https://charts.konghq.com"
  chart            = "kong"
  version          = var.kong_chart_version
  create_namespace = false
  wait             = true
  timeout          = 600

  values = [yamlencode({
    fullnameOverride  = local.kong_name
    replicaCount      = var.kong_replicas
    image             = { repository = "kong/kong-gateway", tag = var.kong_version }
    ingressController = { enabled = false }
    admin             = { enabled = false }
    manager           = { enabled = false }
    secretVolumes     = ["kong-cluster-cert"]

    env = {
      role                          = "data_plane"
      database                      = "off"
      konnect_mode                  = "on"
      vitals                        = "off"
      router_flavor                 = "expressions"
      cluster_mtls                  = "pki"
      cluster_control_plane         = "${local.cp_host}:443"
      cluster_server_name           = local.cp_host
      cluster_telemetry_endpoint    = "${local.tp_host}:443"
      cluster_telemetry_server_name = local.tp_host
      cluster_cert                  = "/etc/secrets/kong-cluster-cert/tls.crt"
      cluster_cert_key              = "/etc/secrets/kong-cluster-cert/tls.key"
      lua_ssl_trusted_certificate   = "system"
      proxy_access_log              = "off"
      # Referenced by plugins as {vault://env/kong-openai-auth-header}
      openai_auth_header = {
        valueFrom = { secretKeyRef = { name = local.secret_name, key = "kong-openai-auth-header" } }
      }
    }

    # /metrics for Prometheus (lab lesson: the Docker run had no status listener)
    status = { enabled = true, http = { enabled = true, containerPort = 8100 } }

    proxy = {
      enabled = true
      type    = "LoadBalancer" # Docker Desktop publishes this on localhost:<servicePort>
      http    = { enabled = true, servicePort = var.proxy_port, containerPort = 8000 }
      tls     = { enabled = false }
    }

    podDisruptionBudget = { enabled = var.kong_replicas > 1, maxUnavailable = 1 }
    resources = {
      requests = { cpu = "250m", memory = "512Mi" }
      limits   = { memory = "1Gi" }
    }
  })]

  depends_on = [
    konnect_gateway_data_plane_client_certificate.dp,
    kubernetes_secret_v1.kong_cluster_cert,
    kubernetes_secret_v1.runtime,
    kubernetes_deployment_v1.redis, # routing vectors + budgets must be reachable at start
  ]
}

# Headless service so Prometheus scrapes EVERY data plane pod (not a load-balanced one)
resource "kubernetes_service_v1" "kong_metrics" {
  metadata {
    name      = "kong-metrics"
    namespace = local.ns
    labels    = { app = "kong-metrics" }
  }
  spec {
    cluster_ip = "None"
    selector   = { "app.kubernetes.io/instance" = local.kong_name }
    port {
      name        = "metrics"
      port        = 8100
      target_port = 8100
    }
  }
}
