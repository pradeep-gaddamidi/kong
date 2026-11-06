#!/usr/bin/env bash
# One wrapper so laptops and CI run Terraform the same way.
#
#   bash scripts/tf.sh <platform|gateway|portal|monitoring> <staging|prod|shared> <init|plan|apply|destroy|output> [extra args]
#
# Order for a new environment:   platform -> (build-gateway.sh) -> gateway -> portal ;  monitoring once ("shared")
# State: local files in ~/.shopglobal-tfstate/<stack>-<env>.tfstate  (outside the repo; swap for a
#        remote backend - HCP Terraform / S3 - when this leaves the laptop).
# Secrets: export TF_VAR_openai_api_key TF_VAR_bot_api_key TF_VAR_eval_api_key and KONNECT_TOKEN first.
set -euo pipefail

STACK="${1:?stack}"; ENV="${2:?env}"; CMD="${3:?command}"; shift 3
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/terraform/stacks/$STACK"
TF="${TF:-terraform}"                       # TF=tofu works too
STATE_DIR="${STATE_DIR:-$HOME/.shopglobal-tfstate}"
[[ -d "$DIR" ]] || { echo "unknown stack: $STACK"; exit 1; }
mkdir -p "$STATE_DIR"

VARS=()
case "$STACK" in
  platform)   VARS=(-var-file="$ROOT/terraform/envs/$ENV.tfvars") ;;
  gateway|portal) VARS=(-var "env=$ENV" -var "state_dir=$STATE_DIR") ;;
  monitoring) ENV=shared ;;
esac

if [[ "$STACK" == "gateway" && "$CMD" =~ ^(plan|apply)$ ]]; then
  grep -q "environment '$ENV'" "$DIR/gateway.generated.tf" 2>/dev/null || {
    echo "gateway.generated.tf is missing or was built for another env - run: bash scripts/build-gateway.sh $ENV"; exit 1; }
fi

# Re-init when switching env (each env has its own state file)
init() { "$TF" -chdir="$DIR" init -input=false -reconfigure -backend-config="path=$STATE_DIR/$STACK-$ENV.tfstate" >/dev/null; }

case "$CMD" in
  init)    init; echo "initialized $STACK ($ENV) -> $STATE_DIR/$STACK-$ENV.tfstate" ;;
  plan)    init; "$TF" -chdir="$DIR" plan  -input=false ${VARS[@]+"${VARS[@]}"} -out="$STACK-$ENV.tfplan" "$@" ;;
  apply)   init
           if [[ -f "$DIR/$STACK-$ENV.tfplan" ]]; then "$TF" -chdir="$DIR" apply -input=false "$STACK-$ENV.tfplan"; rm -f "$DIR/$STACK-$ENV.tfplan"
           else "$TF" -chdir="$DIR" apply -input=false ${VARS[@]+"${VARS[@]}"} "$@"; fi ;;
  destroy) init; "$TF" -chdir="$DIR" destroy ${VARS[@]+"${VARS[@]}"} "$@" ;;
  output)  init; "$TF" -chdir="$DIR" output "$@" ;;
  *) echo "command must be init|plan|apply|destroy|output"; exit 1 ;;
esac
