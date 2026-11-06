# ---------------------------------------------------------------------------
# Monitoring stack (shared by all environments): Prometheus + Grafana in namespace "monitoring".
# Prometheus discovers EVERY Kong data plane pod through the headless kong-metrics service
# in each environment namespace and tags the series with env="staging"|"prod".
# Run once:  bash scripts/tf.sh monitoring shared apply
# ---------------------------------------------------------------------------
terraform {
  required_version = ">= 1.6"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.38"
    }
  }
  backend "local" {}
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig)
  config_context = var.kube_context
}

variable "kubeconfig" {
  type    = string
  default = "~/.kube/config"
}
variable "kube_context" {
  type    = string
  default = "docker-desktop"
}
variable "environments" {
  description = "Environment namespaces to scrape (shopglobal-<env>)"
  type        = list(string)
  default     = ["staging", "prod"]
}
variable "grafana_admin_password" {
  description = "TF_VAR_grafana_admin_password"
  type        = string
  sensitive   = true
  default     = "admin" # local lab only
}

locals {
  ns   = kubernetes_namespace_v1.monitoring.metadata[0].name
  repo = abspath("${path.module}/../../..")
  prometheus_yml = yamlencode({
    global = { scrape_interval = "15s" }
    scrape_configs = [for e in var.environments : {
      job_name     = "kong-dp-${e}"
      metrics_path = "/metrics"
      dns_sd_configs = [{
        names = ["kong-metrics.shopglobal-${e}.svc.cluster.local"]
        type  = "A"
        port  = 8100
      }]
      relabel_configs = [{ target_label = "env", replacement = e }]
    }]
  })
}

resource "kubernetes_namespace_v1" "monitoring" {
  metadata { name = "monitoring" }
}

# ---------------- Prometheus ----------------
resource "kubernetes_config_map_v1" "prometheus" {
  metadata {
    name      = "prometheus-config"
    namespace = local.ns
  }
  data = { "prometheus.yml" = local.prometheus_yml }
}

resource "kubernetes_deployment_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = local.ns
    labels    = { app = "prometheus" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "prometheus" } }
    template {
      metadata {
        labels      = { app = "prometheus" }
        annotations = { "checksum/config" = sha256(local.prometheus_yml) }
      }
      spec {
        container {
          name  = "prometheus"
          image = "prom/prometheus:v2.53.0"
          args  = ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.retention.time=7d"]
          port { container_port = 9090 }
          volume_mount {
            name       = "config"
            mount_path = "/etc/prometheus"
          }
        }
        volume {
          name = "config"
          config_map { name = kubernetes_config_map_v1.prometheus.metadata[0].name }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = local.ns
  }
  spec {
    type     = "LoadBalancer" # http://localhost:9090
    selector = { app = "prometheus" }
    port {
      port        = 9090
      target_port = 9090
    }
  }
}

# ---------------- Grafana (data source + dashboard provisioned from the repo) ----------------
resource "kubernetes_config_map_v1" "grafana_provisioning" {
  metadata {
    name      = "grafana-provisioning"
    namespace = local.ns
  }
  data = {
    "datasource.yml" = file("${local.repo}/observability/grafana/provisioning/datasources/kong-prometheus.yml")
    "dashboards.yml" = file("${local.repo}/observability/grafana/provisioning/dashboards/shopglobal.yml")
  }
}

resource "kubernetes_config_map_v1" "grafana_dashboards" {
  metadata {
    name      = "grafana-dashboards"
    namespace = local.ns
  }
  data = {
    "shopglobal-smart-routing.json" = file("${local.repo}/observability/grafana/dashboards/shopglobal-smart-routing.json")
  }
}

resource "kubernetes_secret_v1" "grafana" {
  metadata {
    name      = "grafana-admin"
    namespace = local.ns
  }
  data = { password = var.grafana_admin_password }
}

resource "kubernetes_deployment_v1" "grafana" {
  metadata {
    name      = "grafana"
    namespace = local.ns
    labels    = { app = "grafana" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "grafana" } }
    template {
      metadata {
        labels = { app = "grafana" }
        annotations = {
          "checksum/dash" = sha256(jsonencode(kubernetes_config_map_v1.grafana_dashboards.data))
        }
      }
      spec {
        container {
          name  = "grafana"
          image = "grafana/grafana:11.1.0"
          port { container_port = 3000 }
          env {
            name = "GF_SECURITY_ADMIN_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.grafana.metadata[0].name
                key  = "password"
              }
            }
          }
          env {
            name  = "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"
            value = "/var/lib/grafana/dashboards/shopglobal-smart-routing.json"
          }
          volume_mount {
            name       = "datasources"
            mount_path = "/etc/grafana/provisioning/datasources"
          }
          volume_mount {
            name       = "dashboard-providers"
            mount_path = "/etc/grafana/provisioning/dashboards"
          }
          volume_mount {
            name       = "dashboards"
            mount_path = "/var/lib/grafana/dashboards"
          }
        }
        volume {
          name = "datasources"
          config_map {
            name = kubernetes_config_map_v1.grafana_provisioning.metadata[0].name
            items {
              key  = "datasource.yml"
              path = "datasource.yml"
            }
          }
        }
        volume {
          name = "dashboard-providers"
          config_map {
            name = kubernetes_config_map_v1.grafana_provisioning.metadata[0].name
            items {
              key  = "dashboards.yml"
              path = "dashboards.yml"
            }
          }
        }
        volume {
          name = "dashboards"
          config_map { name = kubernetes_config_map_v1.grafana_dashboards.metadata[0].name }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "grafana" {
  metadata {
    name      = "grafana"
    namespace = local.ns
  }
  spec {
    type     = "LoadBalancer" # http://localhost:3000
    selector = { app = "grafana" }
    port {
      port        = 3000
      target_port = 3000
    }
  }
}

output "grafana_url" {
  value = "http://localhost:3000"
}
output "prometheus_url" {
  value = "http://localhost:9090"
}
