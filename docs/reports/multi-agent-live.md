# Multi-Agent Coordination Benchmark

- Generated: `2026-07-31T08:44:56.931741+00:00`
- Git SHA: `4f932a6e34ebaa9bcfbc3703167f8af6e2f65146`
- Working tree dirty: `false`
- Mode: `live`
- Repeats per case: `3`
- Cases: `4`
- Total samples: `48`
- Fast model: `gpt-5.6-luna`
- Smart model: `gpt-5.6-terra`
- Judge model: `gpt-5.6-terra`

## Results

| Variant | Pass rate | Quality mean (95% CI) | P50/P95 critical ms | Mean tokens | Mean cost units |
|---|---:|---:|---:|---:|---:|
| `single_agent` | 75.0% | 0.950 (0.900–1.000) | 1506.1/1984.7 | 160.9 | 0.0010 |
| `typed_dag` | 25.0% | 0.804 (0.716–0.885) | 3578.9/7644.5 | 440.2 | 0.0023 |
| `sequential_dag` | 41.7% | 0.787 (0.710–0.863) | 4341.0/6830.8 | 432.2 | 0.0022 |
| `no_synthesis` | 50.0% | 0.806 (0.725–0.879) | 1220.2/1786.0 | 219.8 | 0.0005 |

## Typed DAG deltas

A negative latency/cost delta is better; a positive quality delta is better.

| Compared with | Δ quality | Δ critical ms | Δ tokens | Δ cost units |
|---|---:|---:|---:|---:|
| `single_agent` | -0.146 | +2661.8 | +279.2 | +0.0013 |
| `sequential_dag` | +0.017 | -548.5 | +7.9 | +0.0001 |
| `no_synthesis` | -0.002 | +2872.9 | +220.4 | +0.0018 |

## Interpretation boundary

Live mode calls configured providers and uses a versioned semantic judge; model and prompt drift remain factors.

The JSON artifact next to this report contains every case-level sample, model selection, the fixed bootstrap seed and full distribution statistics.
