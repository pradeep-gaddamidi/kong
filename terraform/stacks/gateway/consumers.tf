# Who may call the gateway. Kept out of the OpenAPI specs on purpose: credentials are secrets.

# The bot backend (the browser never sees this key)
resource "konnect_gateway_consumer" "shop_bot_web" {
  control_plane_id = local.control_plane_id
  username         = "shop-bot-web"
  tags             = ["shopglobal-ai", "platform"]
}

resource "konnect_gateway_key_auth" "shop_bot_web" {
  control_plane_id = local.control_plane_id
  consumer_id      = konnect_gateway_consumer.shop_bot_web.id
  key              = var.bot_api_key
}

# The evaluation harness: also allowed to call the LLM judge route (ACL group "eval")
resource "konnect_gateway_consumer" "eval_runner" {
  control_plane_id = local.control_plane_id
  username         = "eval-runner"
  tags             = ["shopglobal-ai", "platform"]
}

resource "konnect_gateway_key_auth" "eval_runner" {
  control_plane_id = local.control_plane_id
  consumer_id      = konnect_gateway_consumer.eval_runner.id
  key              = var.eval_api_key
}

resource "konnect_gateway_acl" "eval_runner" {
  control_plane_id = local.control_plane_id
  consumer_id      = konnect_gateway_consumer.eval_runner.id
  group            = "eval"
}
