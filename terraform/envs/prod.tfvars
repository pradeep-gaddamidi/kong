# Platform-stack settings for prod. Same Mac cluster, separate namespace + control plane + ports. Secrets: TF_VAR_*.
env                = "prod"
control_plane_name = "shopglobal-prod"
proxy_port         = 8800
bot_port           = 8890
