"""Developer and CI entry points for model-free contract evaluations."""

import argparse
import asyncio
import json

from app.evaluation.base import EvaluationCase
from app.evaluation.fixtures import STARTER_CASES
from app.evaluation.runner import execute_case

FAST_SUITES = (
    "structured_output",
    "router_contract",
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
    parser.add_argument("command", choices=["fast"])
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()
    if args.command == "fast" and not asyncio.run(run_fast(json_output=args.json_output)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
