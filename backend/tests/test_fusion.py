import uuid

from app.rag.fusion import reciprocal_rank_fusion
from app.rag.rerank import _parse_order

A, B, C, D = (uuid.uuid4() for _ in range(4))


def test_agreement_between_lists_wins():
    fused = reciprocal_rank_fusion([[A, B, C], [B, A, D]])
    ranked = [item for item, _ in fused]
    # B is 2nd then 1st, A is 1st then 2nd: both beat items seen in one list only.
    assert set(ranked[:2]) == {A, B}
    assert set(ranked[2:]) == {C, D}


def test_single_ranking_is_preserved():
    fused = reciprocal_rank_fusion([[A, B, C]])
    assert [item for item, _ in fused] == [A, B, C]


def test_item_in_both_lists_beats_a_higher_ranked_singleton():
    # C is top of the second list but appears once; A appears in both.
    fused = reciprocal_rank_fusion([[A, B], [C, A]])
    assert fused[0][0] == A


def test_empty_input():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[]]) == []


class TestParseOrder:
    def test_reads_a_plain_array(self):
        assert _parse_order("[3, 1, 2]", 5) == [2, 0, 1]

    def test_ignores_prose_around_the_array(self):
        assert _parse_order("Here you go: [2, 1]. Hope that helps.", 3) == [1, 0]

    def test_empty_array_means_nothing_relevant(self):
        assert _parse_order("[]", 4) == []

    def test_unusable_reply_is_none(self):
        assert _parse_order("I cannot rank these", 4) is None

    def test_drops_out_of_range_and_duplicate_indices(self):
        assert _parse_order("[1, 1, 99, 2]", 3) == [0, 1]
