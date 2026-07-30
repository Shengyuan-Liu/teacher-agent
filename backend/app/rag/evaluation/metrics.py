"""Retrieval and answer metrics.

Recall@k is computed against the chunk a question was generated from, so it is
exact. Faithfulness and correctness need judgement, so they use an LLM judge
that is given the evidence and asked for a bounded verdict rather than a score
it would have to invent.
"""

import json
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from app.services.providers import IntelligenceTier, chat_model

FAITHFULNESS_PROMPT = """You are checking whether an answer stays within its evidence.

Evidence:
---
{context}
---

Answer:
---
{answer}
---

Break the answer into its factual claims. A claim is supported when the
evidence states or directly implies it. Ignore hedging and pleasantries.

Reply with JSON only: {{"supported": <int>, "total": <int>, "unsupported": ["..."]}}"""

CORRECTNESS_PROMPT = """Compare a candidate answer with a reference answer.

Question: {question}

Reference answer:
---
{reference}
---

Candidate answer:
---
{candidate}
---

Does the candidate convey the same substance as the reference? Wording,
extra detail and formatting do not matter; contradicting or missing the
central point does.

Reply with JSON only: {{"verdict": "correct" | "partial" | "incorrect", "why": "..."}}"""

CORRECTNESS_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


@dataclass
class Judgement:
    score: float
    detail: str


def recall_at_k(retrieved_ids: list[str], gold_id: str, k: int) -> float:
    return 1.0 if gold_id in retrieved_ids[:k] else 0.0


def mrr(retrieved_ids: list[str], gold_id: str) -> float:
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id == gold_id:
            return 1.0 / rank
    return 0.0


async def faithfulness(context: str, answer: str) -> Judgement:
    reply = await chat_model(IntelligenceTier.SMART).ainvoke(
        [HumanMessage(FAITHFULNESS_PROMPT.format(context=context[:12000], answer=answer))]
    )
    parsed = _json(reply.text)
    if not parsed or not parsed.get("total"):
        return Judgement(score=0.0, detail="unparsable judge reply")
    score = parsed["supported"] / parsed["total"]
    unsupported = "; ".join(parsed.get("unsupported", [])[:3])
    return Judgement(score=score, detail=unsupported)


async def correctness(question: str, reference: str, candidate: str) -> Judgement:
    reply = await chat_model(IntelligenceTier.SMART).ainvoke(
        [
            HumanMessage(
                CORRECTNESS_PROMPT.format(
                    question=question, reference=reference, candidate=candidate
                )
            )
        ]
    )
    parsed = _json(reply.text)
    if not parsed:
        return Judgement(score=0.0, detail="unparsable judge reply")
    return Judgement(
        score=CORRECTNESS_SCORE.get(parsed.get("verdict", ""), 0.0),
        detail=parsed.get("why", "")[:200],
    )


def _json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
