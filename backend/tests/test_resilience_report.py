import pytest

from app.evaluation.resilience_report import (
    render_resilience_markdown,
    run_resilience_profile,
    write_resilience_report,
)


@pytest.mark.asyncio
async def test_resilience_profile_exercises_load_and_fault_gates(tmp_path):
    report = await run_resilience_profile(turns=20, concurrency=10)

    assert report["passed"] is True
    assert report["scenarios"]["healthy"]["completed"] == 20
    assert report["scenarios"]["transient_timeout"]["retry_attempts"] == 20
    assert report["scenarios"]["permanent_timeout"]["blocked"] == 20
    assert report["scenarios"]["cache_stampede"]["computations"] == 1
    assert report["scenarios"]["circuit_recovery"]["half_open_admitted"] == 1
    assert "Overall gate: `PASS`" in render_resilience_markdown(report)

    json_path, markdown_path = write_resilience_report(report, tmp_path)
    assert json_path.exists()
    assert markdown_path.exists()
