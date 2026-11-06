output "control_plane_id" {
  description = "Consumed by the gateway stack (terraform_remote_state)"
  value       = konnect_gateway_control_plane.this.id
}

output "control_plane_name" {
  value = konnect_gateway_control_plane.this.name
}

output "control_plane_endpoint" {
  value = konnect_gateway_control_plane.this.config.control_plane_endpoint
}

output "namespace" {
  value = local.ns
}

output "proxy_url" {
  description = "Kong proxy on your Mac (Docker Desktop LoadBalancer)"
  value       = "http://localhost:${var.proxy_port}"
}

output "bot_url" {
  value = "http://localhost:${var.bot_port}"
}

output "dp_cert_expires" {
  value = tls_self_signed_cert.dp.validity_end_time
}

output "ci_system_account_token" {
  description = "Store as GitHub secret KONNECT_SPAT, then: terraform output -raw ci_system_account_token"
  value       = var.create_ci_system_account ? konnect_system_account_access_token.ci[0].token : null
  sensitive   = true
}
