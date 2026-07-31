# Agent Resilience and Load Profile

- Generated: `2026-07-31T08:40:27.798462+00:00`
- Turns per DAG scenario: `200`
- Concurrency: `50`
- Overall gate: `PASS`

## DAG load and failures

| Scenario | Success | Blocked | Retries | Throughput turns/s | P50/P95 ms |
|---|---:|---:|---:|---:|---:|
| `healthy` | 100.0% | 0 | 0 | 8016.8 | 5.36/6.50 |
| `transient_timeout` | 100.0% | 0 | 200 | 1695.8 | 8.47/91.88 |
| `permanent_timeout` | 0.0% | 200 | 200 | 2840.7 | 5.54/49.01 |

## Governance fault injection

- Budget contention: 12 admitted, 38 blocked, no oversubscription.
- Cache stampede: 50 requests produced 1 computation.
- Circuit: 50 calls blocked while open; exactly one half-open probe admitted; success closed the circuit.

## Gates

- [x] `healthy_success_rate`
- [x] `transient_retry_recovery`
- [x] `permanent_failure_propagation`
- [x] `budget_no_oversubscription`
- [x] `cache_single_flight`
- [x] `circuit_half_open_exclusive`

## Interpretation boundary

- This profile exercises real orchestration and governance code without model calls.
- Run the separate HTTP load command against a deployed stack for network and database SLOs.
