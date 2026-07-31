import pytest

from app.evaluation.benchmark_report import (
    bootstrap_mean_ci,
    distribution,
    render_markdown,
    run_coordination_benchmark,
    write_report,
)


def test_distribution_is_deterministic_and_reports_tail_latency():
    values = [1.0, 2.0, 3.0, 10.0]

    stats = distribution(values)

    assert stats["mean"] == 4.0
    assert stats["p50"] == 2.5
    assert stats["p95"] == pytest.approx(8.95)
    assert bootstrap_mean_ci(values) == bootstrap_mean_ci(values)


@pytest.mark.asyncio
async def test_coordination_report_is_reproducible_and_writes_both_artifacts(tmp_path):
    report = await run_coordination_benchmark(repeats=2)

    assert report["samples"] == 32
    assert set(report["summary"]) == {
        "single_agent",
        "typed_dag",
        "sequential_dag",
        "no_synthesis",
    }
    assert report["summary"]["typed_dag"]["metrics"]["answer_quality"]["n"] == 8
    assert "Deterministic mode" in render_markdown(report)

    json_path, markdown_path = write_report(report, tmp_path)
    assert json_path.exists()
    assert markdown_path.exists()
