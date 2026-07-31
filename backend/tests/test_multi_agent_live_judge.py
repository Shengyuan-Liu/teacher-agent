import json

import pytest

from app.evaluation.suites.multi_agent_coordination import _parse_judgement


def test_live_judge_accepts_semantic_scores_with_one_value_per_claim():
    judgement = _parse_judgement(
        json.dumps(
            {
                "claim_scores": [1, 0.5],
                "citation_coverage": 0.75,
                "order_accuracy": 1,
                "coherence": 0.9,
                "reason": "One claim is only partially supported.",
            }
        ),
        2,
    )

    assert judgement["claim_scores"] == [1.0, 0.5]
    assert judgement["coherence"] == 0.9


def test_live_judge_rejects_wrong_claim_count_and_out_of_range_scores():
    with pytest.raises(ValueError, match="number of expected claims"):
        _parse_judgement(
            '{"claim_scores":[1],"citation_coverage":1,"order_accuracy":1,"coherence":1}',
            2,
        )
    with pytest.raises(ValueError, match="between 0 and 1"):
        _parse_judgement(
            '{"claim_scores":[2],"citation_coverage":1,"order_accuracy":1,"coherence":1}',
            1,
        )
