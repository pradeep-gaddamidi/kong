variable "env" {
  type = string
  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod."
  }
}

variable "konnect_server_url" {
  type    = string
  default = "https://us.api.konghq.com"
}

variable "state_dir" {
  description = "Folder holding the local state files (set by scripts/tf.sh)"
  type        = string
  default     = "~/.shopglobal-tfstate"
}

# Secrets: TF_VAR_bot_api_key / TF_VAR_eval_api_key (GitHub secrets in CI). Never in tfvars.
variable "bot_api_key" {
  type      = string
  sensitive = true
}

variable "eval_api_key" {
  type      = string
  sensitive = true
}
