# A/B routing-policy decision

- Control: `reports/sim-control.json` (gate FAIL)
- Candidate: `reports/sim-candidate.json` (gate PASS)

| Metric | Control | Candidate | Better is |
|---|---|---|---|
| Routing accuracy | 0.925 | 1.0 | up |
| Language parity gap | 0.05 | 0.0 | down |
| Under-route rate | 0.0 | 0.0 | down |
| Over-route rate | 0.15 | 0.0 | down |
| Success rate | 1.0 | 1.0 | up |
| Projected savings % | 58.8 | 69.7 | up |
| Projected $ / 1k conversations | 1.5294 | 1.0742 | down |
| Cost / successful answer $ | 0.002248 | 0.0018868 | down |
| p95 latency ms | 3412.8 | 3412.8 | down |

## Decision: **PROMOTE candidate**

- Candidate passes the gate, does not under-route more, and is cheaper or equal per successful answer.
