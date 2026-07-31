"""Reproducible multi-agent ablation reports with distribution statistics."""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.evaluation.base import EvaluationCase
from app.evaluation.fixtures import STARTER_CASES
from app.evaluation.runner import execute_case
from app.evaluation.suites.multi_agent_coordination import VARIANTS
from app.services.providers import IntelligenceTier, model_trace

ExecutionMode = Literal["deterministic", "live"]
REPORT_SCHEMA_VERSION = "1.0.0"


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_mean_ci(
    values: list[float],
    *,
    confidence: float = 0.95,
    samples: int = 2_000,
    seed: int = 20260731,
) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = [statistics.fmean(rng.choice(values) for _ in values) for _sample in range(samples)]
    tail = (1 - confidence) / 2
    return percentile(means, tail), percentile(means, 1 - tail)


def distribution(values: list[float]) -> dict[str, float | int]:
    low, high = bootstrap_mean_ci(values)
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6) if values else 0.0,
        "p50": round(percentile(values, 0.50), 6),
        "p95": round(percentile(values, 0.95), 6),
        "stdev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
        "bootstrap_95_ci_low": round(low, 6),
        "bootstrap_95_ci_high": round(high, 6),
    }


def _git_metadata() -> dict[str, Any]:
    root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    repository_root = root_result.stdout.strip() or None
    configured = os.getenv("GIT_SHA") or os.getenv("GITHUB_SHA")
    if configured:
        sha = configured[:64]
    else:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            cwd=repository_root,
        )
        sha = completed.stdout.strip() or None
    dirty = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            ".",
            ":(exclude)docs/reports",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=repository_root,
    )
    return {"sha": sha, "working_tree_dirty": bool(dirty.stdout.strip())}


def _case(raw: dict[str, Any]) -> EvaluationCase:
    return EvaluationCase(
        key=raw["key"],
        input=raw["input"],
        expected=raw["expected"],
        tags=raw.get("tags", []),
    )


async def run_coordination_benchmark(
    *,
    repeats: int = 30,
    execution_mode: ExecutionMode = "deterministic",
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    raw_cases = STARTER_CASES["multi_agent_coordination"]
    rows: list[dict[str, Any]] = []
    for variant in VARIANTS:
        for repeat in range(1, repeats + 1):
            for raw in raw_cases:
                executed = await execute_case(
                    suite_name="multi_agent_coordination",
                    case=_case(raw),
                    workspace_id=None,
                    config={"variant": variant, "execution_mode": execution_mode},
                )
                if executed.outcome is None:
                    raise RuntimeError(
                        f"{variant}/{raw['key']} failed: {executed.error or 'unknown error'}"
                    )
                output = executed.outcome.output
                rows.append(
                    {
                        "variant": variant,
                        "repeat": repeat,
                        "case": raw["key"],
                        "passed": executed.outcome.passed,
                        "answer_quality": executed.outcome.scores["answer_quality"],
                        "claim_recall": executed.outcome.scores["claim_recall"],
                        "citation_coverage": executed.outcome.scores["citation_coverage"],
                        "critical_path_ms": float(output["critical_path_ms"]),
                        "runner_latency_ms": executed.latency_ms,
                        "tokens": int(output["effective_tokens"]),
                        "cost_units": float(output["effective_cost_units"]),
                        "agent_calls": int(output["agent_calls"]),
                        "answer": output["answer"],
                        "judge": executed.outcome.details.get("judge"),
                    }
                )

    summary: dict[str, Any] = {}
    metrics = (
        "answer_quality",
        "claim_recall",
        "citation_coverage",
        "critical_path_ms",
        "runner_latency_ms",
        "tokens",
        "cost_units",
        "agent_calls",
    )
    for variant in VARIANTS:
        selected = [row for row in rows if row["variant"] == variant]
        summary[variant] = {
            "samples": len(selected),
            "pass_rate": round(sum(row["passed"] for row in selected) / len(selected), 6),
            "metrics": {
                metric: distribution([float(row[metric]) for row in selected]) for metric in metrics
            },
        }

    typed = summary["typed_dag"]["metrics"]
    deltas = {}
    for baseline in ("single_agent", "sequential_dag", "no_synthesis"):
        other = summary[baseline]["metrics"]
        deltas[baseline] = {
            "answer_quality": round(
                typed["answer_quality"]["mean"] - other["answer_quality"]["mean"], 6
            ),
            "critical_path_ms": round(
                typed["critical_path_ms"]["mean"] - other["critical_path_ms"]["mean"], 6
            ),
            "tokens": round(typed["tokens"]["mean"] - other["tokens"]["mean"], 6),
            "cost_units": round(typed["cost_units"]["mean"] - other["cost_units"]["mean"], 6),
        }

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "execution_mode": execution_mode,
        "repeats_per_case": repeats,
        "cases": len(raw_cases),
        "samples": len(rows),
        "git": _git_metadata(),
        "models": {
            "fast": model_trace(IntelligenceTier.FAST),
            "smart": model_trace(IntelligenceTier.SMART),
            "judge": model_trace(IntelligenceTier.SMART),
        },
        "methodology": {
            "variants": list(VARIANTS),
            "bootstrap_samples": 2_000,
            "confidence": 0.95,
            "seed": 20260731,
            "quality_note": (
                "Deterministic mode validates the coordination contract and report pipeline; "
                "live mode is required for claims about model quality."
                if execution_mode == "deterministic"
                else (
                    "Live mode calls configured providers and uses a versioned semantic judge; "
                    "model and prompt drift remain factors."
                )
            ),
        },
        "summary": summary,
        "typed_dag_deltas": deltas,
        "rows": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Multi-Agent Coordination Benchmark",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Git SHA: `{report['git']['sha']}`",
        f"- Working tree dirty: `{str(report['git']['working_tree_dirty']).lower()}`",
        f"- Mode: `{report['execution_mode']}`",
        f"- Repeats per case: `{report['repeats_per_case']}`",
        f"- Cases: `{report['cases']}`",
        f"- Total samples: `{report['samples']}`",
        f"- Fast model: `{report['models']['fast']['model']}`",
        f"- Smart model: `{report['models']['smart']['model']}`",
        f"- Judge model: `{report['models']['judge']['model']}`",
        "",
        "## Results",
        "",
        "| Variant | Pass rate | Quality mean (95% CI) | P50/P95 critical ms | "
        "Mean tokens | Mean cost units |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for variant, result in report["summary"].items():
        metrics = result["metrics"]
        quality = metrics["answer_quality"]
        latency = metrics["critical_path_ms"]
        lines.append(
            f"| `{variant}` | {result['pass_rate']:.1%} | "
            f"{quality['mean']:.3f} "
            f"({quality['bootstrap_95_ci_low']:.3f}–{quality['bootstrap_95_ci_high']:.3f}) | "
            f"{latency['p50']:.1f}/{latency['p95']:.1f} | "
            f"{metrics['tokens']['mean']:.1f} | {metrics['cost_units']['mean']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Typed DAG deltas",
            "",
            "A negative latency/cost delta is better; a positive quality delta is better.",
            "",
            "| Compared with | Δ quality | Δ critical ms | Δ tokens | Δ cost units |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for baseline, delta in report["typed_dag_deltas"].items():
        lines.append(
            f"| `{baseline}` | {delta['answer_quality']:+.3f} | "
            f"{delta['critical_path_ms']:+.1f} | {delta['tokens']:+.1f} | "
            f"{delta['cost_units']:+.4f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            report["methodology"]["quality_note"],
            "",
            "The JSON artifact next to this report contains every case-level sample, model "
            "selection, the fixed bootstrap seed and full distribution statistics.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"multi-agent-{report['execution_mode']}"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
