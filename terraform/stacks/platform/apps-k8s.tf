# ---------------------------------------------------------------------------
# Supporting workloads in the environment namespace:
#   redis-stack     vectors (semantic routing/cache/guard) + budget & rate-limit counters
#   mock-order-api  Order + Catalog upstream (repo code mounted from ConfigMaps)
#   shop-bot        bot backend + demo chat page (holds the Kong consumer key, never the browser)
# Code comes straight from the repo via file(), so a code change = terraform plan shows it.
# ---------------------------------------------------------------------------
locals {
  repo = abspath("${path.module}/../../..")
}

# ---------------- Redis Stack ----------------
resource "kubernetes_deployment_v1" "redis" {
  metadata {
    name      = "redis-stack"
    namespace = local.ns
    labels    = { app = "redis-stack" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "redis-stack" } }
    template {
      metadata { labels = { app = "redis-stack" } }
      spec {
        container {
          name  = "redis"
          image = "redis/redis-stack-server:7.4.0-v1"
          port { container_port = 6379 }
          readiness_probe {
            tcp_socket { port = 6379 }
            initial_delay_seconds = 3
            period_seconds        = 5
          }
          resources {
            requests = { cpu = "100m", memory = "256Mi" }
            limits   = { memory = "512Mi" }
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "redis" {
  metadata {
    name      = "redis-stack"
    namespace = local.ns
  }
  spec {
    selector = { app = "redis-stack" }
    port {
      port        = 6379
      target_port = 6379
    }
  }
}

# ---------------- Mock Order + Catalog API ----------------
resource "kubernetes_config_map_v1" "mock_code" {
  metadata {
    name      = "mock-api-code"
    namespace = local.ns
  }
  data = { "order_api.py" = file("${local.repo}/mock_apis/order_api.py") }
}

resource "kubernetes_config_map_v1" "shop_data" {
  metadata {
    name      = "shop-data"
    namespace = local.ns
  }
  data = {
    "orders.json"   = file("${local.repo}/data/orders.json")
    "products.json" = file("${local.repo}/data/products.json")
  }
}

resource "kubernetes_deployment_v1" "mock_api" {
  metadata {
    name      = "mock-order-api"
    namespace = local.ns
    labels    = { app = "mock-order-api" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "mock-order-api" } }
    template {
      metadata {
        labels = { app = "mock-order-api" }
        # roll the pod when code or data changes
        annotations = {
          "checksum/code" = sha256(jsonencode(kubernetes_config_map_v1.mock_code.data))
          "checksum/data" = sha256(jsonencode(kubernetes_config_map_v1.shop_data.data))
        }
      }
      spec {
        container {
          name    = "mock-api"
          image   = "python:3.11-slim"
          command = ["python3", "/app/mock_apis/order_api.py", "--port", "8081"]
          port { container_port = 8081 }
          # sub_path = a real file, not a ConfigMap symlink: order_api.py finds ../data via Path.resolve()
          volume_mount {
            name       = "code"
            mount_path = "/app/mock_apis/order_api.py"
            sub_path   = "order_api.py"
          }
          volume_mount {
            name       = "data"
            mount_path = "/app/data"
          }
          readiness_probe {
            http_get {
              path = "/health"
              port = 8081
            }
            period_seconds = 5
          }
          resources {
            requests = { cpu = "50m", memory = "64Mi" }
            limits   = { memory = "128Mi" }
          }
        }
        volume {
          name = "code"
          config_map { name = kubernetes_config_map_v1.mock_code.metadata[0].name }
        }
        volume {
          name = "data"
          config_map { name = kubernetes_config_map_v1.shop_data.metadata[0].name }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "mock_api" {
  metadata {
    name      = "mock-order-api"
    namespace = local.ns
  }
  spec {
    selector = { app = "mock-order-api" }
    port {
      port        = 8081
      target_port = 8081
    }
  }
}

# ---------------- Bot backend + demo page ----------------
resource "kubernetes_config_map_v1" "bot_code" {
  metadata {
    name      = "shop-bot-code"
    namespace = local.ns
  }
  data = {
    "app.py"     = file("${local.repo}/bot/app.py")
    "index.html" = file("${local.repo}/bot/index.html")
  }
}

resource "kubernetes_config_map_v1" "bot_prices" {
  metadata {
    name      = "shop-bot-prices"
    namespace = local.ns
  }
  data = { "prices.json" = file("${local.repo}/eval/prices.json") }
}

resource "kubernetes_deployment_v1" "bot" {
  metadata {
    name      = "shop-bot"
    namespace = local.ns
    labels    = { app = "shop-bot" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "shop-bot" } }
    template {
      metadata {
        labels = { app = "shop-bot" }
        annotations = {
          "checksum/code" = sha256(jsonencode(kubernetes_config_map_v1.bot_code.data))
        }
      }
      spec {
        container {
          name    = "bot"
          image   = "python:3.11-slim"
          command = ["python3", "/app/bot/app.py"]
          port { container_port = 8090 }
          env {
            name  = "KONG_PROXY_URL"
            value = "http://${local.kong_proxy}:${var.proxy_port}"
          }
          env {
            name = "DECK_BOT_API_KEY"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.runtime.metadata[0].name
                key  = "bot-api-key"
              }
            }
          }
          # sub_path mounts: app.py resolves /app/eval/prices.json and index.html from its real location
          volume_mount {
            name       = "code"
            mount_path = "/app/bot/app.py"
            sub_path   = "app.py"
          }
          volume_mount {
            name       = "code"
            mount_path = "/app/bot/index.html"
            sub_path   = "index.html"
          }
          volume_mount {
            name       = "prices"
            mount_path = "/app/eval"
          }
          readiness_probe {
            http_get {
              path = "/health"
              port = 8090
            }
            period_seconds = 5
          }
          resources {
            requests = { cpu = "50m", memory = "64Mi" }
            limits   = { memory = "128Mi" }
          }
        }
        volume {
          name = "code"
          config_map { name = kubernetes_config_map_v1.bot_code.metadata[0].name }
        }
        volume {
          name = "prices"
          config_map { name = kubernetes_config_map_v1.bot_prices.metadata[0].name }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "bot" {
  metadata {
    name      = "shop-bot"
    namespace = local.ns
  }
  spec {
    type     = "LoadBalancer" # Docker Desktop: http://localhost:<bot_port>
    selector = { app = "shop-bot" }
    port {
      port        = var.bot_port
      target_port = 8090
    }
  }
}
