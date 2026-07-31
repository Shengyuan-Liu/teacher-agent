# Multi-Agent Coordination Benchmark

- Generated: `2026-07-31T08:40:28.433652+00:00`
- Git SHA: `4f932a6e34ebaa9bcfbc3703167f8af6e2f65146`
- Working tree dirty: `false`
- Mode: `deterministic`
- Repeats per case: `30`
- Cases: `4`
- Total samples: `480`
- Fast model: `gpt-5.6-luna`
- Smart model: `gpt-5.6-terra`
- Judge model: `gpt-5.6-terra`

## Results

| Variant | Pass rate | Quality mean (95% CI) | P50/P95 critical ms | Mean tokens | Mean cost units |
|---|---:|---:|---:|---:|---:|
| `single_agent` | 75.0% | 0.885 (0.855–0.915) | 267.5/290.0 | 725.0 | 0.7250 |
| `typed_dag` | 100.0% | 1.000 (1.000–1.000) | 250.0/260.0 | 885.0 | 0.8850 |
| `sequential_dag` | 100.0% | 1.000 (1.000–1.000) | 330.0/340.0 | 885.0 | 0.8850 |
| `no_synthesis` | 100.0% | 0.908 (0.907–0.909) | 115.0/150.0 | 450.0 | 0.4500 |

## Typed DAG deltas

A negative latency/cost delta is better; a positive quality delta is better.

| Compared with | Δ quality | Δ critical ms | Δ tokens | Δ cost units |
|---|---:|---:|---:|---:|
| `single_agent` | +0.115 | -18.8 | +160.0 | +0.1600 |
| `sequential_dag` | +0.000 | -75.0 | +0.0 | +0.0000 |
| `no_synthesis` | +0.092 | +130.0 | +435.0 | +0.4350 |

## Interpretation boundary

Deterministic mode validates the coordination contract and report pipeline; live mode is required for claims about model quality.

The JSON artifact next to this report contains every case-level sample, model selection, the fixed bootstrap seed and full distribution statistics.
