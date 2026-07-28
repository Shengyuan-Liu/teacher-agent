import pytest

from app.agents.outline import _parse as parse_outline
from app.agents.planner import parse_stages
from app.agents.quiz import validate_question


class TestQuizValidation:
    def test_valid_single_choice_passes(self):
        cleaned = validate_question(
            {
                "type": "single",
                "stem": "2+2?",
                "options": ["3", "4", "5", "6"],
                "answer": "4",
                "explanation": "arithmetic",
                "source": 1,
            },
            section_count=3,
        )
        assert cleaned["type"] == "single"
        assert cleaned["source_index"] == 1

    def test_answer_missing_from_options_is_dropped(self):
        assert (
            validate_question(
                {
                    "type": "single",
                    "stem": "?",
                    "options": ["a", "b", "c"],
                    "answer": "d",
                    "explanation": "",
                    "source": 1,
                },
                section_count=1,
            )
            is None
        )

    def test_multi_needs_two_answers_within_options(self):
        assert (
            validate_question(
                {
                    "type": "multi",
                    "stem": "?",
                    "options": ["a", "b", "c", "d"],
                    "answer": ["a"],
                    "explanation": "",
                    "source": 1,
                },
                section_count=1,
            )
            is None
        )
        assert (
            validate_question(
                {
                    "type": "multi",
                    "stem": "?",
                    "options": ["a", "b", "c", "d"],
                    "answer": ["a", "b"],
                    "explanation": "",
                    "source": 1,
                },
                section_count=1,
            )
            is not None
        )

    def test_fill_requires_a_blank_in_the_stem(self):
        assert (
            validate_question(
                {
                    "type": "fill",
                    "stem": "No blank here",
                    "answer": "x",
                    "explanation": "",
                    "source": 1,
                },
                section_count=1,
            )
            is None
        )

    def test_source_out_of_range_is_dropped(self):
        assert (
            validate_question(
                {"type": "short", "stem": "?", "answer": "yes", "explanation": "", "source": 9},
                section_count=3,
            )
            is None
        )

    def test_unknown_difficulty_defaults_to_medium(self):
        cleaned = validate_question(
            {
                "type": "short",
                "stem": "?",
                "answer": "yes",
                "difficulty": "极难",
                "explanation": "",
                "source": 1,
            },
            section_count=1,
        )
        assert cleaned["difficulty"] == "medium"


class TestPlanParsing:
    def test_reads_stages_and_clamps_junk(self):
        stages = parse_stages(
            '{"stages": [{"title": "Basics", "description": "Read ch. 1.",'
            ' "topics": ["Norms"], "activities": ["read", "dance"], "estimated_minutes": 90},'
            ' {"description": "no title, dropped"}]}'
        )
        assert len(stages) == 1
        assert stages[0]["activities"] == ["read"]

    def test_prose_around_the_json_is_tolerated(self):
        stages = parse_stages(
            'Here is the plan:\n{"stages": [{"title": "T", "description": "D"}]}\nGood luck!'
        )
        assert stages[0]["estimated_minutes"] == 60

    def test_no_stages_raises(self):
        with pytest.raises(ValueError):
            parse_stages('{"stages": []}')


class TestOutlineParsing:
    def test_keeps_topics_with_id_and_title(self):
        parsed = parse_outline('{"topics": [{"id": "t1", "title": "Norms"}, {"title": "no id"}]}')
        assert [t["id"] for t in parsed["topics"]] == ["t1"]

    def test_garbage_is_none(self):
        assert parse_outline("not json") is None
        assert parse_outline('{"topics": []}') is None
