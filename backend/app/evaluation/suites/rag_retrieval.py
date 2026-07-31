"""Adapter that brings the existing hybrid RAG benchmark into the platform."""

from app.evaluation.base import EvaluationCase, EvaluationContext, EvaluationOutcome, SuiteInfo
from app.evaluation.registry import register
from app.rag.evaluation.metrics import mrr, recall_at_k
from app.rag.retriever import RetrievalConfig, retrieve

VARIANTS = {
    "dense_only": dict(use_dense=True, use_sparse=False, use_rerank=False),
    "sparse_only": dict(use_dense=False, use_sparse=True, use_rerank=False),
    "hybrid_rrf": dict(use_dense=True, use_sparse=True, use_rerank=False),
    "dense_rerank": dict(use_dense=True, use_sparse=False, use_rerank=True),
    "hybrid_rrf_rerank": dict(use_dense=True, use_sparse=True, use_rerank=True),
}


class RagRetrievalSuite:
    info = SuiteInfo(
        name="rag_retrieval",
        description="Measures Recall@K and MRR for dense, sparse, hybrid and reranked retrieval.",
        metrics=("recall@1", "recall@3", "recall@5", "mrr"),
        requires_workspace=True,
        requires_model=True,
    )

    async def evaluate(self, case: EvaluationCase, context: EvaluationContext) -> EvaluationOutcome:
        if context.workspace_id is None:
            raise ValueError("rag_retrieval requires a workspace")
        question = case.input.get("question")
        gold_id = case.expected.get("gold_parent_id")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("rag_retrieval input.question must be a non-empty string")
        if not isinstance(gold_id, str) or not gold_id:
            raise ValueError("rag_retrieval expected.gold_parent_id must be a string")

        variant = str(context.config.get("variant") or "hybrid_rrf_rerank")
        if variant not in VARIANTS:
            raise ValueError(f"Unknown RAG variant: {variant}")
        overrides = VARIANTS[variant] | {
            key: context.config[key] for key in ("top_k", "candidates") if key in context.config
        }
        hits = await retrieve(context.workspace_id, question, RetrievalConfig(**overrides))
        retrieved_ids = [hit.chunk_id for hit in hits]
        scores = {
            "recall@1": recall_at_k(retrieved_ids, gold_id, 1),
            "recall@3": recall_at_k(retrieved_ids, gold_id, 3),
            "recall@5": recall_at_k(retrieved_ids, gold_id, 5),
            "mrr": mrr(retrieved_ids, gold_id),
        }
        pass_at = int(context.config.get("pass_at", 5))
        passed = recall_at_k(retrieved_ids, gold_id, pass_at) == 1.0
        return EvaluationOutcome(
            passed=passed,
            output={
                "variant": variant,
                "retrieved": [
                    {
                        "chunk_id": hit.chunk_id,
                        "source_title": hit.source_title,
                        "heading": hit.heading,
                        "score": hit.score,
                    }
                    for hit in hits
                ],
            },
            scores=scores,
            details={"gold_parent_id": gold_id, "pass_at": pass_at},
        )


register(RagRetrievalSuite())
