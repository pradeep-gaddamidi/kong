#!/usr/bin/env bash
# Start (or restart) the Kong data plane for Konnect control plane `shopglobal-staging`.
#
# The cluster certificate and key are read from FILES outside this project folder,
# so no secrets live in the repo and nothing multi-line has to be pasted into a command:
#   ~/kong-dp-certs/tls.crt   (-----BEGIN CERTIFICATE----- ... -----END CERTIFICATE-----)
#   ~/kong-dp-certs/tls.key   (-----BEGIN PRIVATE KEY----- ... -----END PRIVATE KEY-----)
#
# What this adds compared with Konnect's generated command:
#   --network / --network-alias  -> Kong starts ON the project network, so it can resolve
#                                   redis-stack and mock-order-api (no DNS restart workaround)
#   KONG_STATUS_LISTEN + -p 8100 -> /metrics endpoint for Prometheus
#
# Usage:  bash scripts/run-kong-dp.sh
set -euo pipefail

CERT_DIR="${CERT_DIR:-$HOME/kong-dp-certs}"
CP_HOST="1007dac6f3.us.cp.konghq.com"        # from Konnect > shopglobal-staging > data plane node
TP_HOST="1007dac6f3.us.tp.konghq.com"
NETWORK="shopglobal-smart-routing_default"   # created by: docker compose up -d
IMAGE="kong/kong-gateway:3.16"

for f in tls.crt tls.key; do
  [[ -s "$CERT_DIR/$f" ]] || { echo "Missing $CERT_DIR/$f"; exit 1; }
done
grep -q "BEGIN CERTIFICATE" "$CERT_DIR/tls.crt" || { echo "tls.crt does not look like a certificate"; exit 1; }
grep -q "PRIVATE KEY" "$CERT_DIR/tls.key"       || { echo "tls.key does not look like a private key"; exit 1; }
docker network inspect "$NETWORK" >/dev/null 2>&1 || { echo "Network $NETWORK not found - run: docker compose up -d redis-stack mock-order-api prometheus grafana"; exit 1; }

docker rm -f kong-dp >/dev/null 2>&1 || true

docker run -d \
  --name kong-dp \
  --restart unless-stopped \
  --network "$NETWORK" \
  --network-alias kong-dp \
  -v "$CERT_DIR:/etc/kong/cluster:ro" \
  -e "KONG_ROLE=data_plane" \
  -e "KONG_DATABASE=off" \
  -e "KONG_VITALS=off" \
  -e "KONG_CLUSTER_MTLS=pki" \
  -e "KONG_CLUSTER_CONTROL_PLANE=$CP_HOST:443" \
  -e "KONG_CLUSTER_SERVER_NAME=$CP_HOST" \
  -e "KONG_CLUSTER_TELEMETRY_ENDPOINT=$TP_HOST:443" \
  -e "KONG_CLUSTER_TELEMETRY_SERVER_NAME=$TP_HOST" \
  -e "KONG_CLUSTER_CERT=/etc/kong/cluster/tls.crt" \
  -e "KONG_CLUSTER_CERT_KEY=/etc/kong/cluster/tls.key" \
  -e "KONG_LUA_SSL_TRUSTED_CERTIFICATE=system" \
  -e "KONG_KONNECT_MODE=on" \
  -e "KONG_ROUTER_FLAVOR=expressions" \
  -e "KONG_STATUS_LISTEN=0.0.0.0:8100" \
  -p 8000:8000 \
  -p 8443:8443 \
  -p 8100:8100 \
  "$IMAGE"

echo "Started kong-dp. Waiting 20s, then checking..."
sleep 20
docker ps --filter name=kong-dp --format '{{.Names}}  {{.Status}}'
curl -s -o /dev/null -w "metrics -> %{http_code}\n" localhost:8100/metrics || true
echo "If it is not Up, run: docker logs kong-dp 2>&1 | tail -30"
