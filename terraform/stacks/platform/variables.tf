variable "env" {
  description = "Environment name: staging or prod"
  type        = string
  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod."
  }
}

variable "konnect_server_url" {
  description = "Konnect API for your region (us/eu/au/...)"
  type        = string
  default     = "https://us.api.konghq.com"
}

variable "control_plane_name" {
  description = "Konnect control plane name (must be unique in the org)"
  type        = string
}

# ---------------- Kubernetes ----------------
variable "kubeconfig" {
  type    = string
  default = "~/.kube/config"
}

variable "kube_context" {
  description = "kubectl context: docker-desktop (Docker Desktop Kubernetes) or kind-<name>"
  type        = string
  default     = "docker-desktop"
}

variable "kong_version" {
  type    = string
  default = "3.16"
}

variable "kong_chart_version" {
  description = "Version of the kong/kong Helm chart (pin it; check `helm search repo kong/kong`)"
  type        = string
  default     = "3.4.1"
}

variable "kong_replicas" {
  description = "Data plane replicas (2+ = high availability)"
  type        = number
  default     = 2
}

variable "proxy_port" {
  description = "Port the Kong proxy LoadBalancer exposes on localhost (staging 8000, prod 8800)"
  type        = number
}

variable "bot_port" {
  description = "Port the demo bot page exposes on localhost (staging 8090, prod 8890)"
  type        = number
}

# ---------------- Secrets (pass via TF_VAR_... environment variables, never in tfvars) ----------------
variable "openai_api_key" {
  description = "OpenAI project key. Stored only in a Kubernetes Secret; Kong reads it via {vault://env/...}."
  type        = string
  sensitive   = true
}

variable "bot_api_key" {
  description = "Kong key-auth key for the bot backend consumer (same value as the gateway stack)."
  type        = string
  sensitive   = true
}

# ---------------- CI identity ----------------
variable "create_ci_system_account" {
  description = "Create a Konnect system account + token for CI/CD (scoped to this control plane)."
  type        = bool
  default     = false
}

variable "ci_token_expires_at" {
  description = "Expiry for the CI system account token (RFC3339). Rotate before it expires."
  type        = string
  default     = "2027-06-30T00:00:00Z"
}

variable "konnect_region" {
  description = "Region code used for role assignments (us, eu, au, ...)"
  type        = string
  default     = "us"
}
