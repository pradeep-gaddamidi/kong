# ShopGlobal Smart Routing AI Gateway

Hands-on build for **Use Case 5 – Shopping Assistant** (Kong AI Expert Partner Certificate Program).

ShopGlobal's bot serves customers in **English and Spanish**. The business wants **o4-mini** for complex
questions and the much cheaper **mistral-tiny** for simple "Check Order Status" queries. This repo is the
Kong AI Gateway that makes that choice automatically, **plus the evidence that it saves money without
hurting answers**. That evidence comes from the evaluation approach in the AWS workshop
*From Prototype to Production – Agentic AI Evaluation and Observability*, applied to the gateway.

> **Models used (OpenAI only, one API key):** OpenAI shut down o4-mini on **2026-10-23**, so this build uses
> **`gpt-6.1-sol`** as the *premium* tier and **`gpt-6-luna`** as the *cheap* tier, both from OpenAI. The test
> set is labelled by **tier**, not model name, so the next model swap is a change to `eval/prices.json` and
> `kong/kong.yaml` only. Embeddings: `text-embedding-3-small`.

> One-line pitch: *we didn't just cut the bill, we measured that quality held in both languages, and the
> pipeline blocks any routing change that makes it worse.*

---

## What's in here

```
shopglobal-smart-routing/
├── kong/
│   ├── kong.yaml               # decK config: semantic routing, cost limits, cache, guards, Order API
│   └── candidate-route.yaml    # A/B candidate routing policy (v2 descriptions, same threshold)
├── data/
│   ├── routing_testset.json    # 40 bilingual cases (20 EN/ES pairs) labelled with expected tier (cheap / premium)
│   ├── orders.json             # sample orders   (from the AWS workshop)
│   └── products.json           # sample catalog  (from the AWS workshop)
├── eval/
│   ├── run_routing_eval.py     # harness: run (simulate|live) + compare (A/B decision)
│   ├── gate.json               # release-gate thresholds
│   ├── prices.json             # tier → model mapping, prices, baseline assumptions
│   └── traffic_mix.json        # assumed production mix for cost projection
├── bot/
│   ├── app.py                  # BOT BACKEND: fetches facts via Kong, calls Kong AI route, reports model/cost
│   └── index.html              # demo chat page (EN/ES, Check Order Status button, model + cost badges)
├── mock_apis/order_api.py      # mock Order + Catalog API (upstream for /api/orders, /api/products)
├── observability/prometheus.yml# scrape config + Grafana queries for AI metrics
├── reports/                    # sample SIMULATED reports (control, candidate, A/B decision)
├── docker-compose.yml          # Konnect DP + Redis Stack + mock API + Prometheus/Grafana + Keycloak
├── apiops/                     # OpenAPI specs + AI policies, patches, lint rules (APIOps source of truth)
├── terraform/stacks/           # platform | gateway | portal | monitoring   (+ terraform/envs/*.tfvars)
├── scripts/                    # build-gateway.sh, tf.sh, wait-for-gateway.sh, postprocess_kong2tf.py
├── .github/workflows/          # pr-checks.yml (no secrets) + deploy.yml (eval-gated, self-hosted Mac runner)
├── ci/eval-gated-deploy.yml    # earlier decK-sync pipeline (superseded, kept for reference)
└── .env.example
```

## Architecture

```
 Shopper (EN/ES)
      │
 ShopGlobal bot backend (bot/app.py) ── fetches facts ──► Kong /api/orders, /api/products ──► mock API
      │   (puts facts in a system message,                    key-auth, rate-limiting, proxy-cache-advanced,
      │    Cache-Control: no-store when order data is present) request/response transformer (strips PII)
      ▼
 Kong AI Gateway
   /assistant/chat            key-auth → ai-prompt-guard (regex) → ai-semantic-prompt-guard (off-topic)
                              → ai-rate-limiting-advanced ($ budget) → ai-prompt-decorator (bilingual)
                              → ai-semantic-cache → ai-proxy-advanced  [algorithm: semantic]
                                                         ├─ "order status…/estado del pedido…" → gpt-6-luna   (cheap)
                                                         └─ "compare, recommend, refund…"      → gpt-6.1-sol  (premium)
                                                         (failover on error/timeout/429/503)
   /assistant/order-status    button traffic: no embedding call, straight to gpt-6-luna     (hybrid, ADR-1)
   /eval/judge                ACL "eval" only: gpt-6.1-sol as LLM-as-judge for the pipeline
   prometheus (ai_metrics)    cost / tokens / latency / cache hits per model & consumer → Grafana
```

## Quick start: offline, about 30 seconds, no keys

The harness has a **simulated router**, so the whole evaluation and reporting flow runs without a gateway.
Simulated answers are templated, not real model output. Reports say so.

```bash
python3 eval/run_routing_eval.py --target simulate --out reports/sim-control.json
python3 eval/run_routing_eval.py --target simulate --sim-policy candidate --out reports/sim-candidate.json
python3 eval/run_routing_eval.py compare reports/sim-control.json reports/sim-candidate.json
```

What you'll see (this is the demo story):

| | Control policy | Candidate policy |
|---|---|---|
| Routing accuracy | 92.5% | 100% |
| EN / ES accuracy | 95% / 90% (**parity gap 5%**) | 100% / 100% |
| Over-route rate (cost leak) | 15% → **gate FAIL** | 0% → gate PASS |
| Projected savings vs. no gateway | 58.8% | 69.7% |
| Decision | – | **PROMOTE** |

The control policy sends Spanish slang (*"oye ya mandaron mis cosas??"*) and bare order numbers to
the premium model. The gate catches the cost leak, the v2 target descriptions fix it, and the A/B compare promotes
the candidate.

## Try it end to end: the bot

`bot/app.py` is the **bot backend**, the only component holding the Kong key (`DECK_BOT_API_KEY`). For each
customer message it:
1. finds order numbers and fetches them **through Kong** `GET /api/orders/{id}` (PII already stripped)
2. finds products mentioned (EN/ES words) and fetches them **through Kong** `GET /api/products?q=`
3. calls Kong `POST /assistant/chat` (or `/assistant/order-status` for the button) with the facts as a system
   message and `Cache-Control: no-store` when order data is present
4. returns the answer **plus what Kong decided**: model, tier, cost, latency, cache, blocked

```bash
set -a; source .env; set +a
python3 bot/app.py                    # then open http://localhost:8090
# or in Docker: docker compose up -d bot   (uses KONG_PROXY_URL=http://kong-dp:8000)
```

> Demo limitation: there is no customer login, so any order number can be looked up. A production bot must
> verify the order belongs to the signed-in customer (Keycloak/OIDC) before fetching it.

## Running against a real gateway

1. **Konnect:** create control planes `shopglobal-staging` (and `shopglobal-prod`), then add a Docker
   data-plane node and copy the cert, key and endpoints into `.env` (`cp .env.example .env`).
2. **Stack:** `docker compose up -d` starts the data plane, Redis Stack, mock Order API, Prometheus,
   Grafana and Keycloak.
3. **Config:**
   ```bash
   set -a; source .env; set +a
   deck file validate kong/kong.yaml
   deck gateway validate kong/kong.yaml --konnect-token "$KONNECT_TOKEN" --konnect-control-plane-name shopglobal-staging
   deck gateway sync kong/kong.yaml kong/candidate-route.yaml --konnect-token "$KONNECT_TOKEN" --konnect-control-plane-name shopglobal-staging
   ```
4. **Smoke test:**
   ```bash
   curl -s localhost:8000/api/orders/ORD-2024-10002 -H "apikey: $DECK_BOT_API_KEY"     # no email/address in output
   curl -si localhost:8000/assistant/chat -H "apikey: $DECK_BOT_API_KEY" -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"¿Dónde está mi pedido ORD-2024-10002?"}]}' | grep -i x-kong-llm-model
   ```
5. **Evaluate:**
   ```bash
   export KONG_PROXY_URL=http://localhost:8000 KONG_API_KEY=$DECK_EVAL_API_KEY
   python3 eval/run_routing_eval.py --target live --hybrid --judge --facts-via-kong
   ```
   `--facts-via-kong` makes the harness fetch order/product facts through Kong like the bot does (and warns if
   PII fields come back); without it, facts are read from `data/*.json`.
6. **A/B a routing change:**
   ```bash
   python3 eval/run_routing_eval.py --target live --route /assistant/chat           --no-cache --out reports/control.json
   python3 eval/run_routing_eval.py --target live --route /assistant/chat-candidate --no-cache --out reports/candidate.json
   python3 eval/run_routing_eval.py compare reports/control.json reports/candidate.json
   ```

For the OIDC path (Keycloak), get a token and pass it with `KONG_BEARER_TOKEN` instead of `KONG_API_KEY`,
after swapping `key-auth` for `openid-connect` on the customer-facing routes.

## The metrics, and why they matter for a "terrified of the bill" customer

| Metric | Meaning |
|---|---|
| **Routing accuracy** (by slice, by language) | Did Kong choose the tier we labelled? |
| **Under-route rate** | Complex question sent to the cheap tier: *quality risk*, so the gate is strict (≤ 5%) |
| **Over-route rate** | Simple question sent to the premium tier: *cost leak* (≤ 10%) |
| **Language parity gap** | EN accuracy minus ES accuracy. Spanish is where intent routing usually breaks |
| **Pair consistency** | Same question in EN and ES lands on the same tier |
| **Guard block rate** | Abuse/off-topic prompts stopped before *any* model spend |
| **Cost per successful answer** | Cost ÷ answers that passed the checks. Cheap-but-wrong doesn't count |
| **Projected $ / 1k conversations** | Test-set costs re-weighted by `traffic_mix.json` vs. a no-gateway, all-premium baseline |
| **Gateway embedding overhead** | What semantic routing, caching and guarding cost on top. Small, but stated honestly |

## How the AWS workshop maps onto this

| Workshop (AgentCore) | Here (Kong AI Gateway) |
|---|---|
| `evaluation_dataset.json` + slices | `routing_testset.json` with `expected_tier`, slices, EN/ES pairs |
| Pyramid Layer 1: deterministic assertions | served model (`X-Kong-LLM-Model`), blocked?, must-have facts, answer language |
| Layer 2: custom LLM-as-judge evaluators | `--judge`: goal success / helpfulness / language match via `/eval/judge` |
| Layer 3: human review | "Needs human review" table in every report |
| Batch-eval release gate (Module 03) | `eval-gate` job in GitHub Actions before `deck gateway sync` to prod |
| Config-bundle A/B (Module 05) | `candidate-route.yaml` + `compare` → PROMOTE / HOLD |
| Online evidence loop (Module 04) | Prometheus AI metrics + misroute review → new test cases / better descriptions |
| RBAC interceptor (customer vs admin) | Kong consumers + ACL (e.g. `/eval/judge` and the candidate route are eval-only) |

## Design decisions (ADRs for the Blueprint)

- **ADR-1 Hybrid routing.** Button traffic already knows its intent, so a deterministic route skips the
  embedding call. Free text uses the semantic balancer.
- **ADR-2 Bilingual target descriptions.** Route by *intent*, not language, with no language-detection
  step. One decorator system prompt makes both models reply in the customer's language.
- **ADR-3 Dollar budgets, not token budgets.** `ai-rate-limiting-advanced` with `tokens_count_strategy: cost`,
  using the same `input_cost`/`output_cost` that the eval uses.
- **ADR-4 Cache privacy.** The semantic cache is shared across users, so the bot sends `Cache-Control: no-store`
  whenever order data is in the prompt. Only generic product Q&A is cached.
- **ADR-5 Eval-gated config.** A routing change is a model-quality change and is released like one.
- **ADR-6 Minimise PII.** Kong strips email and address from the Order API; `log_payloads: false` on every target.

## Open questions and things to check before the demo

1. **Model availability on your key.** Run
   `curl -s https://api.openai.com/v1/models -H "Authorization: Bearer $DECK_OPENAI_API_KEY" | grep -E "gpt-6-luna|gpt-6.1-sol|text-embedding-3-small"`.
   OpenAI's deprecations page names `gpt-5.6-terra` as o4-mini's official replacement; it was not on the pricing
   page on 2026-10-25, so `gpt-6.1-sol` is used. Swap in `prices.json` + `kong.yaml` if you prefer it.
2. **Prices** (OpenAI pricing page, 2026-10-25): gpt-6.1-sol $2.00/$10.00, gpt-6-luna $0.10/$0.50 per 1M tokens.
   The embeddings price ($0.02/1M) was not shown on that page; verify. Set a **monthly budget on the OpenAI
   project** that owns the key, as a backstop to the gateway budgets.
3. **New-model parameters.** If OpenAI rejects `max_tokens` or other options for a newer model (HTTP 400 via Kong),
   remove that option from the target in `kong.yaml`.
4. **Plugin schemas** were written against Kong 3.11/3.12 docs. Run `deck gateway validate` against your
   control plane, especially for `ai-semantic-prompt-guard`, `ai-semantic-cache` and the `failover_criteria` field.
5. **Thresholds are maximum cosine *distances*** (lower = stricter), not similarities: router 1.0 (closest
   description always wins; 0.75 caused 503 "No peers are available" on long questions), cache 0.1, guard 0.5.
   Tune them with the harness.
6. **What gets embedded:** check whether your Kong version embeds the whole conversation or only the last
   user message for semantic routing. If it embeds the injected system facts, routing may drift. The
   eval will show it.
7. **Traffic mix** (70% order status) is an assumption. Replace it with real Prometheus numbers.
8. **Baseline assumptions** (premium-model reasoning tokens on simple queries, blocked prompts answered in full)
   are estimates in `prices.json` → `baseline`. State them on the slide.

## Operationalized setup: APIOps + Terraform + Kubernetes + CI/CD

The hand-run lab (decK sync + one `docker run` data plane) is now a repeatable pipeline. **Nothing is
created by hand any more**: every Konnect object, every Kubernetes object and every gateway entity comes
from files in this repo.

```
OpenAPI specs (API teams)          AI policies / patches (platform team)
apiops/openapi/*.yaml              apiops/policies, platform, patches, lint
          │                                   │
          └──── scripts/build-gateway.sh <env> ────┘
                deck file lint → openapi2kong → add-plugins → merge → patch → lint + validate → kong2tf
                          │
                          ▼
          terraform/stacks/gateway/gateway.generated.tf   (services, routes, 14 kinds of plugins)

terraform/stacks/
  platform/    Konnect control plane, DP certificate (generated + pinned), optional CI system account,
               Kubernetes namespace, secrets, Kong data planes (Helm kong/kong 3.4.1), Redis Stack,
               mock Order/Catalog API, bot
  gateway/     generated gateway config + consumers/credentials (secrets from TF_VAR_*)
  portal/      Dev Portal: the SAME OpenAPI specs published as API docs, linked to their services
  monitoring/  Prometheus (discovers every DP pod, labels env=staging|prod) + Grafana with the dashboard
```

| Reviewer ask | How it is done here |
|---|---|
| **OpenAPI / APIOps** | Specs carry `x-kong-*` extensions; routes are generated with `openapi2kong`. AI plugins are attached by selector (`add-plugins`) so API teams never edit AI policy. Lint rules block specs without `operationId`/security and gateway configs with plaintext provider keys or missing cost settings. The same specs feed the Dev Portal. |
| **Terraform** (instead of decK sync) | One tool, one state, one plan for *everything*: control plane, certificates, data planes, Kubernetes apps, gateway entities, portal. decK stays as the **compiler** (OpenAPI → Kong → Terraform), not the deployer. |
| **Kubernetes** | Data planes run in Kubernetes from the official Helm chart (2 replicas + PodDisruptionBudget in prod), one namespace per environment. The DP cert is created by Terraform and mounted from a Secret, so nobody copies keys around. The OpenAI key is a Secret read by Kong's env vault (`{vault://env/kong-openai-auth-header}`). |
| **CI/CD** | `.github/workflows/pr-checks.yml`: build both envs, `terraform validate`, simulated eval, no secrets. `.github/workflows/deploy.yml`: on `main`, apply staging, run the **live eval gate**, then prod after approval. |

### Why Terraform instead of `deck gateway sync`?

decK is excellent for gateway config, but it only knows about gateway entities. Here the release also
needs a control plane, a pinned data-plane certificate, Kubernetes Secrets, Helm releases, Redis, and a
portal. Terraform puts all of that in **one dependency graph** (the cert is generated → pinned in Konnect →
mounted into the pods in the same apply), gives a **plan to review in the PR**, detects **drift** when
someone clicks in the UI, and handles **secrets as sensitive variables**. Kong's own APIOps guidance is
the pattern used here: keep OpenAPI + decK file tooling for the build, use `kong2tf` to hand the result to
Terraform.

### Environments

| | staging | prod |
|---|---|---|
| Control plane | `shopglobal-staging-tf` | `shopglobal-prod` |
| Namespace | `shopglobal-staging` | `shopglobal-prod` |
| Kong proxy / bot | http://localhost:8000 / :8090 | http://localhost:8800 / :8890 |
| DP replicas | 1 | 2 (+ PDB) |
| Budget (chat route) | $5/h, $50/day | $50/h, $500/day |
| A/B candidate route | yes | no |

Grafana http://localhost:3000 and Prometheus http://localhost:9090 (namespace `monitoring`) serve both.

### First run on the Mac

1. **Free the ports the old lab uses**: `docker rm -f kong-dp && docker compose down`.
2. **Turn on Kubernetes**: Docker Desktop → Settings → Kubernetes → Enable → Apply.
   Check: `kubectl config use-context docker-desktop && kubectl get nodes`.
3. **Install tools**: `brew install hashicorp/tap/terraform kubectl helm kong/deck/deck && pip3 install pyyaml` (Helm is only for `helm search`;
   Terraform's Helm provider does the install).
4. **Secrets in your shell** (never in files that go to Git):
   ```bash
   export KONNECT_TOKEN=kpat_...            # Konnect personal access token
   export TF_VAR_openai_api_key=sk-...
   export TF_VAR_bot_api_key=...  TF_VAR_eval_api_key=...   # any long random strings
   ```
5. **Deploy staging**:
   ```bash
   bash scripts/tf.sh platform staging plan  &&  bash scripts/tf.sh platform staging apply
   bash scripts/build-gateway.sh staging
   bash scripts/tf.sh gateway  staging plan  &&  bash scripts/tf.sh gateway  staging apply
   bash scripts/tf.sh portal   staging apply
   bash scripts/tf.sh monitoring shared apply
   bash scripts/wait-for-gateway.sh http://localhost:8000 "$TF_VAR_eval_api_key"
   KONG_API_KEY=$TF_VAR_eval_api_key python3 eval/run_routing_eval.py --target live --hybrid --judge
   ```
6. **Prod**: the same with `prod` (after `bash scripts/build-gateway.sh prod`).

State files live in `~/.shopglobal-tfstate/` (outside the repo). They contain the DP private key and
secrets, so in a real setup use an encrypted remote backend (HCP Terraform, S3 + KMS).

### CI/CD runner

PR checks run on GitHub-hosted runners. Deploys need the Mac (cluster + state), so register it as a
**self-hosted runner** with labels `self-hosted, macOS, shopglobal` (repo → Settings → Actions → Runners).
Add secrets `KONNECT_TOKEN`, `OPENAI_API_KEY`, `BOT_API_KEY`, `EVAL_API_KEY`, and create environments
`staging` and `production` (add *Required reviewers* to `production`). For least privilege, set
`create_ci_system_account = true` in the platform stack and use its token instead of your personal one.

## Next steps

- Move Terraform state to an encrypted remote backend and the runner off the laptop (EKS/GKE/AKS data planes:
  only `kube_context` and the proxy Service type change).
- Expose the workshop's product MCP server through `ai-mcp-proxy` (Blueprint section 12).
- Mine Prometheus/OTEL data for suspected over-routes (premium-tier calls with tiny completions) and add the
  reviewed prompts to the test set.
