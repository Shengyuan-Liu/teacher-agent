from datetime import date

import pytest

from app.agents.outline import _parse as parse_outline
from app.agents.outline import _render_structure
from app.agents.planner import finalise_stages, order_stages_by_outline, parse_stages
from app.agents.quiz import deduplicate_questions, parse_supported, validate_question


class TestQuizValidation:
    def test_grounding_reply_is_fail_closed_and_range_checked(self):
        assert parse_supported('{"supported": [1, 3, 3, 99, true]}', 3) == {1, 3}
        assert parse_supported("not json", 3) == set()

    def test_duplicate_stems_are_removed_before_semantic_validation(self):
        questions = [
            {"stem": "What is A?", "answer": "a"},
            {"stem": " what is a ", "answer": "different"},
            {"stem": "What is B?", "answer": "b"},
        ]
        assert [q["answer"] for q in deduplicate_questions(questions)] == ["a", "b"]

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

    def test_material_order_is_enforced_after_generation(self):
        outline = [
            {"title": "Discrete-time Markov chains"},
            {"title": "Properties of the exponential distribution"},
            {"title": "Poisson processes"},
            {"title": "Continuous-time Markov chains"},
            {"title": "Brownian motion"},
        ]
        generated = [
            {"title": "CTMC", "topics": ["Continuous-time Markov chains"]},
            {"title": "Poisson", "topics": ["Poisson processes"]},
            {"title": "Exponential", "topics": ["Properties of the exponential distribution"]},
            {
                "title": "Review",
                "topics": ["Discrete-time Markov chains", "Brownian motion"],
            },
        ]

        ordered = order_stages_by_outline(generated, outline)

        assert [stage["title"] for stage in ordered] == [
            "Exponential",
            "Poisson",
            "CTMC",
            "Review",
        ]

    def test_daily_titles_are_renumbered_after_ordering(self):
        outline = [{"title": "Poisson"}, {"title": "CTMC"}]
        generated = [
            {"title": "第1天：CTMC", "topics": ["CTMC"]},
            {"title": "第2天：Poisson", "topics": ["Poisson"]},
        ]

        ordered = order_stages_by_outline(generated, outline)

        assert [stage["title"] for stage in ordered] == ["第1天：Poisson", "第2天：CTMC"]

    def test_finaliser_canonicalises_topics_and_caps_daily_work(self):
        outline = [{"title": "Poisson Processes"}, {"title": "CTMC"}]
        generated = [
            {
                "title": "Day 1: Poisson",
                "description": "Read it",
                "topics": [" poisson   processes ", "invented"],
                "activities": [],
                "estimated_minutes": 300,
            },
            {
                "title": "Day 2: CTMC",
                "description": "Practise",
                "topics": ["ctmc"],
                "activities": ["quiz", "quiz"],
                "estimated_minutes": 120,
            },
        ]
        result = finalise_stages(generated, outline, daily_minutes=90)
        assert result[0]["topics"] == ["Poisson Processes"]
        assert result[0]["activities"] == ["read"]
        assert result[0]["estimated_minutes"] == 90
        assert result[1]["activities"] == ["quiz"]
        assert result[1]["estimated_minutes"] == 90

    def test_finaliser_fits_a_feasible_deadline_budget(self):
        stages = [
            {
                "title": "A",
                "description": "A",
                "topics": ["A"],
                "activities": ["read"],
                "estimated_minutes": 100,
            },
            {
                "title": "B",
                "description": "B",
                "topics": ["B"],
                "activities": ["read"],
                "estimated_minutes": 100,
            },
        ]
        result = finalise_stages(
            stages,
            [{"title": "A"}, {"title": "B"}],
            daily_minutes=60,
            deadline=date(2026, 1, 2),
            today=date(2026, 1, 1),
        )
        assert sum(stage["estimated_minutes"] for stage in result) <= 120


class TestOutlineParsing:
    def test_keeps_topics_with_id_and_title(self):
        parsed = parse_outline('{"topics": [{"id": "t1", "title": "Norms"}, {"title": "no id"}]}')
        assert [t["id"] for t in parsed["topics"]] == ["t1"]

    def test_garbage_is_none(self):
        assert parse_outline("not json") is None
        assert parse_outline('{"topics": []}') is None

    def test_future_dependencies_are_removed_and_ids_are_canonical(self):
        parsed = parse_outline(
            '{"topics": ['
            '{"id": "intro", "title": "Intro", "depends_on": ["later"]},'
            '{"id": "later", "title": "Later", "depends_on": ["intro"]}'
            "]}"
        )

        assert parsed["topics"] == [
            {"id": "t1", "title": "Intro", "summary": "", "depends_on": []},
            {"id": "t2", "title": "Later", "summary": "", "depends_on": ["t1"]},
        ]

    def test_plain_text_pdf_fallback_keeps_toc_and_document_order(self):
        structure = _render_structure(
            [
                (
                    "Applied Probability.pdf",
                    0,
                    None,
                    "Contents\n3 Discrete-time Markov chains\n"
                    "4 Properties of the exponential distribution\n"
                    "5 Poisson processes\n6 Continuous-time Markov chains\n"
                    "7 Brownian motion",
                ),
                ("Applied Probability.pdf", 1, None, "Chapter 3\nDiscrete-time Markov chains"),
                ("Applied Probability.pdf", 2, None, "Chapter 5\nPoisson processes"),
                ("Applied Probability.pdf", 3, None, "Chapter 6\nContinuous-time Markov chains"),
            ]
        )

        assert "Contents" in structure
        assert structure.index("5 Poisson processes") < structure.index(
            "6 Continuous-time Markov chains"
        )
        assert "[document position 3]" in structure
