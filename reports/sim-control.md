# ShopGlobal Smart Routing - eval report

- Run: `2026-10-25T21:41:08+00:00`  |  target: **simulate** (policy `control`, SIMULATED - not real model output)  |  route: `/assistant/chat`  |  hybrid: False  |  judge: False
- Gate: **FAIL**

## Headline

| Metric | Value |
|---|---|
| Routing accuracy | 92.5% (EN 95.0% / ES 90.0%) |
| Language parity gap | 5.0% |
| EN/ES pair consistency | 95.0% |
| Under-route rate (complex -> cheap tier, quality risk) | 0.0% |
| Over-route rate (simple -> premium tier, cost leak) | 15.0% |
| Guard block rate | 100.0% |
| Success rate (Layer 1) | 100.0% |
| Share served by cheap tier (gpt-6-luna) | 47.2% |
| Projected cost per 1k conversations (traffic mix) | $1.5294 vs $3.7138 with no gateway / all premium |
| **Projected savings** | 58.8% (test-set mix: 54.3%) |
| **Cost per successful answer** | $0.002248 |
| Gateway embedding overhead | $0.000664 of $0.080928 total |
| p50 / p95 latency | 1751.1 / 3412.8 ms (premium mean 2581.9 ms) |

## Gate

| Check | Value | Rule | Result |
|---|---|---|---|
| routing_accuracy | 0.925 | >= 0.9 | pass |
| under_route_rate | 0.0 | <= 0.05 | pass |
| over_route_rate | 0.15 | <= 0.1 | **FAIL** |
| language_parity_gap | 0.05 | <= 0.05 | pass |
| guard_block_rate | 1.0 | >= 1.0 | pass |
| success_rate | 1.0 | >= 0.85 | pass |
| projected_savings_pct | 58.8 | >= 30.0 | pass |

## By slice

| Slice | Routing accuracy |
|---|---|
| borderline | 100.0% |
| complex | 100.0% |
| guardrail | 100.0% |
| order_status | 85.0% |

## Needs human review (Layer 3)

| Case | Lang | Expected tier | Served | Why flagged | Prompt |
|---|---|---|---|---|---|
| RT-ES-009 | es | cheap | premium (gpt-6.1-sol) | over-route | oye ya mandaron mis cosas?? ORD-2024-10009 |
| RT-EN-010 | en | cheap | premium (gpt-6.1-sol) | over-route | order ORD-2024-10008? |
| RT-ES-010 | es | cheap | premium (gpt-6.1-sol) | over-route | pedido ORD-2024-10008? |
