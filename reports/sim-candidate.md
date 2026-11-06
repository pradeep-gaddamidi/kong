# ShopGlobal Smart Routing - eval report

- Run: `2026-10-25T21:41:08+00:00`  |  target: **simulate** (policy `candidate`, SIMULATED - not real model output)  |  route: `/assistant/chat`  |  hybrid: False  |  judge: False
- Gate: **PASS**

## Headline

| Metric | Value |
|---|---|
| Routing accuracy | 100.0% (EN 100.0% / ES 100.0%) |
| Language parity gap | 0.0% |
| EN/ES pair consistency | 100.0% |
| Under-route rate (complex -> cheap tier, quality risk) | 0.0% |
| Over-route rate (simple -> premium tier, cost leak) | 0.0% |
| Guard block rate | 100.0% |
| Success rate (Layer 1) | 100.0% |
| Share served by cheap tier (gpt-6-luna) | 55.6% |
| Projected cost per 1k conversations (traffic mix) | $1.0742 vs $3.5398 with no gateway / all premium |
| **Projected savings** | 69.7% (test-set mix: 60.5%) |
| **Cost per successful answer** | $0.0018868 |
| Gateway embedding overhead | $0.000664 of $0.067923 total |
| p50 / p95 latency | 647.6 / 3412.8 ms (premium mean 2461.7 ms) |

## Gate

| Check | Value | Rule | Result |
|---|---|---|---|
| routing_accuracy | 1.0 | >= 0.9 | pass |
| under_route_rate | 0.0 | <= 0.05 | pass |
| over_route_rate | 0.0 | <= 0.1 | pass |
| language_parity_gap | 0.0 | <= 0.05 | pass |
| guard_block_rate | 1.0 | >= 1.0 | pass |
| success_rate | 1.0 | >= 0.85 | pass |
| projected_savings_pct | 69.7 | >= 30.0 | pass |

## By slice

| Slice | Routing accuracy |
|---|---|
| borderline | 100.0% |
| complex | 100.0% |
| guardrail | 100.0% |
| order_status | 100.0% |

## Needs human review (Layer 3)

None - all cases routed correctly and answered successfully.
