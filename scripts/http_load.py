#!/usr/bin/env python3
"""Small dependency-light HTTP load probe for deployed readiness SLOs."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

import httpx


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


async def run_load(
    *,
    url: str,
    requests: int,
    concurrency: int,
    timeout_seconds: float,
) -> dict:
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:

        async def request_once() -> tuple[int | str, float]:
            async with semaphore:
                started = perf_counter()
                try:
                    response = await client.get(url)
                    status: int | str = response.status_code
                except httpx.HTTPError as exc:
                    status = type(exc).__name__
                return status, (perf_counter() - started) * 1000

        started = perf_counter()
        results = await asyncio.gather(*(request_once() for _ in range(requests)))
        wall_seconds = perf_counter() - started

    latencies = [latency for _status, latency in results]
    successes = sum(isinstance(status, int) and 200 <= status < 300 for status, _ in results)
    return {
        "target": url,
        "requests": requests,
        "concurrency": concurrency,
        "successes": successes,
        "availability": round(successes / requests, 6),
        "throughput_requests_per_second": round(requests / wall_seconds, 3),
        "latency_ms": {
            "mean": round(sum(latencies) / len(latencies), 3),
            "p50": round(percentile(latencies, 0.50), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "statuses": dict(Counter(str(status) for status, _latency in results)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a deployed TeacherAgent HTTP SLO")
    parser.add_argument(
        "--url",
        default="http://localhost:8000/api/v1/health/ready",
    )
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--min-availability", type=float, default=0.99)
    parser.add_argument("--max-p95-ms", type=float, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")

    report = asyncio.run(
        run_load(
            url=args.url,
            requests=args.requests,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
        )
    )
    report["slo"] = {
        "min_availability": args.min_availability,
        "max_p95_ms": args.max_p95_ms,
        "passed": report["availability"] >= args.min_availability
        and report["latency_ms"]["p95"] <= args.max_p95_ms,
    }
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if not report["slo"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
