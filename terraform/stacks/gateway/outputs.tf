# Consumed by the portal stack to link each published API to its gateway service
output "service_ids" {
  value = {
    orders-api     = konnect_gateway_service.orders_api.id
    catalog-api    = konnect_gateway_service.catalog_api.id
    shop-assistant = konnect_gateway_service.shop_assistant.id
  }
}

output "control_plane_id" {
  value = local.control_plane_id
}
