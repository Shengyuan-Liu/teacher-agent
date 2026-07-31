"""Developer and CI entry points for model-free contract evaluations."""

import argparse
import asyncio
import json
from pathlib import Path

from app.evaluation.base import EvaluationCase
from app.evaluation.benchmark_report import run_coordination_benchmark, write_report
from app.evaluation.fixtures import STARTER_CASES
from app.evaluation.resilience_report import (
    run_resilience_profile,
    write_resilience_report,
)
from app.evaluation.runner import execute_case

FAST_SUITES = (
    "structured_output",
    "router_contract",
    "agent_security",
    "resource_governance",
    "multi_agent_coordination",
)


async def run_fast(*, json_output: bool = False) -> bool:
    reports: dict[str, dict] = {}
    all_passed = True
    for suite in FAST_SUITES:
        failures = []
        cases = STARTER_CASES[suite]
        for raw in cases:
            result = await execute_case(
                suite_name=suite,
                case=EvaluationCase(
                    key=raw["key"],
                    input=raw["input"],
                    expected=raw["expected"],
                    tags=raw.get("tags", []),
                ),
                workspace_id=None,
                config={},
            )
            if result.outcome is None or not result.outcome.passed:
                failures.append({"key": raw["key"], "error": result.error})
        reports[suite] = {
            "cases": len(cases),
            "passed": len(cases) - len(failures),
            "failures": failures,
        }
        all_passed = all_passed and not failures

    if json_output:
        print(json.dumps(reports, indent=2))
    else:
        for suite, report in reports.items():
            print(f"{suite}: {report['passed']}/{report['cases']} passed")
            for failure in report["failures"]:
                print(f"  FAIL {failure['key']}: {failure['error'] or 'contract mismatch'}")
    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="TeacherAgent evaluation CLI")
    parser.add_argument("command", choices=["fast", "benchmark", "resilience"])
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--mode", choices=["deterministic", "live"], default="deterministic")
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=Path("../docs/reports"))
    parser.add_argument("--turns", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=50)
    args = parser.parse_args()
    if args.command == "fast" and not asyncio.run(run_fast(json_output=args.json_output)):
        raise SystemExit(1)
    if args.command == "benchmark":
        report = asyncio.run(
            run_coordination_benchmark(
                repeats=args.repeats,
                execution_mode=args.mode,
            )
        )
        paths = write_report(report, args.output_dir)
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            print("\n".join(str(path) for path in paths))
    if args.command == "resilience":
        report = asyncio.run(
            run_resilience_profile(
                turns=args.turns,
                concurrency=args.concurrency,
            )
        )
        paths = write_resilience_report(report, args.output_dir)
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            print("\n".join(str(path) for path in paths))
        if not report["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
