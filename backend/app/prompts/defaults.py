"""Code-owned prompt baselines.

Imports stay lazy in the registry so Agent modules can use the registry without
creating import cycles. Bumping a built-in prompt requires a new immutable
version here, not editing a previously released version number in place.
"""

from app.prompts.registry import BuiltinPrompt


def default_prompts() -> tuple[BuiltinPrompt, ...]:
    from app.agents.lecture import (
        GRADE_SYSTEM,
        INPUT_SYSTEM,
        OUTLINE_SYSTEM,
        SECTION_SYSTEM,
    )
    from app.agents.planner import CHAT_PLAN_PROMPT, DRAFT_PROMPT
    from app.agents.router import CLASSIFY_PROMPT
    from app.evaluation.suites.multi_agent_coordination import (
        _JUDGE_SYSTEM,
        _SYNTHESIS_SYSTEM,
        _WORKER_SYSTEM,
    )
    from app.services.chat_stream import MULTI_AGENT_ANSWER_SYSTEM

    return (
        BuiltinPrompt(
            key="router.classify",
            version=1,
            description="Classify a Chat request and build a typed task plan.",
            template=CLASSIFY_PROMPT,
        ),
        BuiltinPrompt(
            key="answer.multi_source",
            version=1,
            description="Synthesize Web and local evidence into one cited answer.",
            template=MULTI_AGENT_ANSWER_SYSTEM,
        ),
        BuiltinPrompt(
            key="planner.draft",
            version=1,
            description="Draft a new ordered study plan from an outline.",
            template=DRAFT_PROMPT,
        ),
        BuiltinPrompt(
            key="planner.revise",
            version=1,
            description="Revise the full study plan through Chat.",
            template=CHAT_PLAN_PROMPT,
        ),
        BuiltinPrompt(
            key="lecture.outline",
            version=1,
            description="Create an ordered, grounded lecture outline.",
            template=OUTLINE_SYSTEM,
        ),
        BuiltinPrompt(
            key="lecture.section",
            version=1,
            description="Teach one grounded lecture section and create a check.",
            template=SECTION_SYSTEM,
        ),
        BuiltinPrompt(
            key="lecture.classify_input",
            version=1,
            description="Distinguish a lecture check answer from an interruption.",
            template=INPUT_SYSTEM,
        ),
        BuiltinPrompt(
            key="lecture.grade",
            version=1,
            description="Grade one lecture understanding check.",
            template=GRADE_SYSTEM,
        ),
        BuiltinPrompt(
            key="benchmark.worker",
            version=1,
            description="Extract source-specific evidence in the live benchmark.",
            template=_WORKER_SYSTEM,
        ),
        BuiltinPrompt(
            key="benchmark.synthesis",
            version=1,
            description="Synthesize worker evidence in the live benchmark.",
            template=_SYNTHESIS_SYSTEM,
        ),
        BuiltinPrompt(
            key="benchmark.judge",
            version=1,
            description="Judge semantic claim coverage in the live coordination benchmark.",
            template=_JUDGE_SYSTEM,
        ),
    )
