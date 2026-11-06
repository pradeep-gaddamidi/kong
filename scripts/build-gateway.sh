#!/usr/bin/env bash
# APIOps build: OpenAPI specs + platform AI policies  ->  one gateway config per environment
#                                                     ->  Terraform resources (kong2tf)
#
#   bash scripts/build-gateway.sh staging
#   bash scripts/build-gateway.sh prod
#
# Outputs (build/ is git-ignored; CI uploads it as an artifact):
#   build/<env>/kong.yaml                       reviewable decK file (what will be deployed)
#   terraform/stacks/gateway/gateway.generated.tf   Terraform resources for that env (overwritten each run)
#
# Pipeline (Kong APIOps pattern):
#   1 lint OpenAPI specs        deck file lint
#   2 convert specs             deck file openapi2kong
#   3 attach AI policies        deck file add-plugins   (platform-owned policy files)
#   4 merge platform files      deck file merge         (eval judge, global plugins, staging-only A/B route)
#   5 environment patches       deck file patch
#   6 lint + validate result    deck file lint / deck file validate
#   7 compile to Terraform      deck file kong2tf  (+ wire control_plane_id to the platform stack)
set -euo pipefail

ENV="${1:?usage: build-gateway.sh <staging|prod>}"
[[ "$ENV" == "staging" || "$ENV" == "prod" ]] || { echo "env must be staging or prod"; exit 1; }
DECK="${DECK:-deck}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
A="$ROOT/apiops"
OUT="$ROOT/build/$ENV"
TF_OUT="$ROOT/terraform/stacks/gateway/gateway.generated.tf"
mkdir -p "$OUT"
d() { "$DECK" --analytics=false "$@"; }

echo "== 1. Lint OpenAPI specs"
for spec in "$A"/openapi/*.yaml; do
  d file lint -s "$spec" --fail-severity error --display-only-failures "$A/lint/openapi-rules.yaml"
  echo "   ok  $(basename "$spec")"
done

echo "== 2. Convert OpenAPI -> decK"
for spec in "$A"/openapi/*.yaml; do
  d file openapi2kong -s "$spec" -o "$OUT/$(basename "$spec" .yaml).kong.yaml"
done

echo "== 3. Attach platform AI policies to the assistant routes"
d file add-plugins -s "$OUT/shop-assistant-api.kong.yaml" -o "$OUT/shop-assistant-api.policies.kong.yaml" \
  "$A/policies/ai-assistant.plugins.yaml"

echo "== 4. Merge with platform files"
FILES=("$OUT/orders-api.kong.yaml" "$OUT/catalog-api.kong.yaml" "$OUT/shop-assistant-api.policies.kong.yaml"
       "$A/platform/eval-judge.yaml" "$A/platform/global-plugins.yaml")
if [[ "$ENV" == "staging" ]]; then FILES+=("$A"/platform/staging-only/*.yaml); fi
d file merge -o "$OUT/merged.kong.yaml" "${FILES[@]}"

echo "== 5. Apply $ENV patches"
d file patch -s "$OUT/merged.kong.yaml" -o "$OUT/kong.yaml" "$A/patches/$ENV.yaml"

echo "== 6. Lint + validate the built config"
d file lint -s "$OUT/kong.yaml" --fail-severity error --display-only-failures "$A/lint/gateway-rules.yaml"
d file validate "$OUT/kong.yaml"
python3 - "$OUT/kong.yaml" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("   (counts skipped: install PyYAML with  python3 -m pip install --user pyyaml)")
    sys.exit(0)
c = yaml.safe_load(open(sys.argv[1]))
svcs = c.get("services", [])
routes = sum(len(s.get("routes", [])) for s in svcs) + len(c.get("routes", []))
def count(o):
    n = 0
    if isinstance(o, dict):
        n += len(o.get("plugins", [])) if isinstance(o.get("plugins"), list) else 0
        for v in o.values(): n += count(v) if isinstance(v, (dict, list)) else 0
    elif isinstance(o, list):
        for v in o: n += count(v)
    return n
print(f"   built: {len(svcs)} services, {routes} routes, {count(c)} plugins")
PY

echo "== 7. Compile to Terraform (kong2tf)"
d file kong2tf -s "$OUT/kong.yaml" -o "$OUT/gateway.kong2tf.tf"
python3 "$ROOT/scripts/postprocess_kong2tf.py" "$OUT/gateway.kong2tf.tf" "$TF_OUT" "$ENV"
for tf in "${TF:-terraform}" tofu; do command -v "$tf" >/dev/null && { "$tf" fmt "$TF_OUT" >/dev/null; break; }; done
echo "   wrote $TF_OUT"
echo "Done: review $OUT/kong.yaml, then: bash scripts/tf.sh gateway $ENV plan"
