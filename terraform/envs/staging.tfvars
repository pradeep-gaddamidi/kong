# Platform-stack settings for staging (gateway/portal only need env, passed by tf.sh). Secrets: TF_VAR_*.
env                = "staging"
control_plane_name = "shopglobal-staging-tf" # new name: the hand-made "shopglobal-staging" CP stays untouched
proxy_port         = 8000
bot_port           = 8090
kong_replicas      = 1 # keep the Mac light; prod runs 2 (HA)
