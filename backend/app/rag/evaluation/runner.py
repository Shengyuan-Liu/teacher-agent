"""Evaluation harness.

Runs a named retrieval configuration over the golden set and writes one JSON
report plus a Markdown summary into logs/, so successive runs can be compared.

    uv run python -m app.rag.evaluation.runner build --workspace "Optimisation"
    uv run python -m app.rag.evaluation.runner run --variant hybrid_rerank
"""

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.agents.qa import answer_question
from app.core.database import AsyncSessionLocal
from app.models import Workspace
from app.rag.evaluation import dataset, metrics
from app.rag.evaluation.dataset import EvalCase
from app.rag.retriever import RetrievalConfig, retrieve

ROOT = Path(__file__).resolve().parents[4]
LOG_DIR = ROOT / "logs"
DATASET_PATH = ROOT / "backend" / "app" / "rag" / "evaluation" / "golden_set.json"

VARIANTS: dict[str, RetrievalConfig] = {
    "dense_only": RetrievalConfig(use_dense=True, use_sparse=False, use_rerank=False),
    "sparse_only": RetrievalConfig(use_dense=False, use_sparse=True, use_rerank=False),
    "hybrid_rrf": RetrievalConfig(use_dense=True, use_sparse=True, use_rerank=False),
    "dense_rerank": RetrievalConfig(use_dense=True, use_sparse=False, use_rerank=True),
    "hybrid_rrf_rerank": RetrievalConfig(use_dense=True, use_sparse=True, use_rerank=True),
}


@dataclass
class CaseResult:
    question: str
    gold_parent_id: str | None
    retrieved: list[str] = field(default_factory=list)
    recall_at_1: float | None = None
    recall_at_3: float | None = None
    recall_at_5: float | None = None
    mrr: float | None = None
    faithfulness: float | None = None
    correctness: float | None = None
    declined: bool | None = None
    note: str = ""


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


async def run_variant(
    variant: str, cases: list[EvalCase], workspace_id: uuid.UUID, judge: bool
) -> dict:
    config = VARIANTS[variant]
    results: list[CaseResult] = []

    for case in cases:
        hits = await retrieve(workspace_id, case.question, config)
        retrieved = [h.chunk_id for h in hits]
        result = CaseResult(
            question=case.question, gold_parent_id=case.gold_parent_id, retrieved=retrieved
        )

        if case.gold_parent_id:
            result.recall_at_1 = metrics.recall_at_k(retrieved, case.gold_parent_id, 1)
            result.recall_at_3 = metrics.recall_at_k(retrieved, case.gold_parent_id, 3)
            result.recall_at_5 = metrics.recall_at_k(retrieved, case.gold_parent_id, 5)
            result.mrr = metrics.mrr(retrieved, case.gold_parent_id)

        if judge:
            answer, grounded = await answer_question(case.question, workspace_id, config)
            result.declined = not grounded
            if case.gold_parent_id is None:
                result.note = "out of scope"
            else:
                context = "\n\n".join(h.content for h in hits)
                faith = await metrics.faithfulness(context, answer)
                corr = await metrics.correctness(case.question, case.reference_answer or "", answer)
                result.faithfulness = round(faith.score, 4)
                result.correctness = corr.score
                result.note = corr.detail

        results.append(result)

    in_scope = [r for r in results if r.gold_parent_id]
    out_scope = [r for r in results if r.gold_parent_id is None]
    summary = {
        "variant": variant,
        "cases": len(results),
        "recall@1": _mean([r.recall_at_1 for r in in_scope if r.recall_at_1 is not None]),
        "recall@3": _mean([r.recall_at_3 for r in in_scope if r.recall_at_3 is not None]),
        "recall@5": _mean([r.recall_at_5 for r in in_scope if r.recall_at_5 is not None]),
        "mrr": _mean([r.mrr for r in in_scope if r.mrr is not None]),
        "faithfulness": _mean([r.faithfulness for r in in_scope if r.faithfulness is not None]),
        "correctness": _mean([r.correctness for r in in_scope if r.correctness is not None]),
        "decline_rate_out_of_scope": _mean(
            [1.0 if r.declined else 0.0 for r in out_scope if r.declined is not None]
        ),
    }
    return {"summary": summary, "cases": [asdict(r) for r in results]}


def write_report(reports: list[dict], label: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": label,
        "variants": reports,
    }
    json_path = LOG_DIR / f"rag-eval-{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    columns = [
        "variant",
        "recall@1",
        "recall@3",
        "recall@5",
        "mrr",
        "faithfulness",
        "correctness",
        "decline_rate_out_of_scope",
    ]
    lines = [
        f"# RAG evaluation — {label}",
        "",
        f"Generated {payload['generated_at']}  ·  {reports[0]['summary']['cases']} cases",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] * len(columns)) + "|",
    ]
    for report in reports:
        s = report["summary"]
        lines.append("| " + " | ".join(str(s.get(c, "")) for c in columns) + " |")
    md_path = LOG_DIR / f"rag-eval-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n")
    return md_path


async def _workspace_id(name: str | None) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        query = select(Workspace)
        if name:
            query = query.where(Workspace.name.ilike(f"%{name}%"))
        workspace = (await db.scalars(query)).first()
        if workspace is None:
            raise SystemExit("no matching workspace")
        return workspace.id


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "run"])
    parser.add_argument("--workspace")
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--variant", action="append")
    parser.add_argument("--label", default="run")
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()

    workspace_id = await _workspace_id(args.workspace)

    if args.command == "build":
        async with AsyncSessionLocal() as db:
            cases = await dataset.build(db, workspace_id, args.size)
        dataset.save(cases, DATASET_PATH)
        print(f"wrote {len(cases)} cases to {DATASET_PATH}")
        return

    cases = dataset.load(DATASET_PATH)
    variants = args.variant or list(VARIANTS)
    reports = []
    for variant in variants:
        report = await run_variant(variant, cases, workspace_id, judge=not args.no_judge)
        reports.append(report)
        print(json.dumps(report["summary"], indent=2))
    path = write_report(reports, args.label)
    print(f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
