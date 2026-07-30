"""Mastery evidence and spaced-repetition scheduling."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import PlanStage, ReviewItem, StudyPlan, TopicMastery


def topic_from_snapshot(snapshot: dict) -> str:
    source = snapshot.get("source") or {}
    return str(source.get("heading") or source.get("title") or "General")[:500]


def next_review_schedule(
    repetitions: int, interval_days: int, ease_factor: float, correct: bool
) -> tuple[int, int, float]:
    """A compact SM-2 variant with deterministic, bounded intervals."""
    if not correct:
        return 0, 1, max(1.3, ease_factor - 0.2)
    repetitions += 1
    if repetitions == 1:
        interval = 1
    elif repetitions == 2:
        interval = 6
    else:
        interval = max(1, round(interval_days * ease_factor))
    return repetitions, min(interval, 365), min(3.0, ease_factor + 0.05)


async def record_mastery(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    topic: str,
    score_fraction: float,
) -> TopicMastery:
    row = await db.scalar(
        select(TopicMastery).where(
            TopicMastery.workspace_id == workspace_id,
            TopicMastery.user_id == user_id,
            TopicMastery.topic == topic,
        )
    )
    evidence = max(0.0, min(1.0, score_fraction))
    if row is None:
        row = TopicMastery(
            workspace_id=workspace_id,
            user_id=user_id,
            topic=topic,
            score=round(evidence * 100, 2),
            attempts=1,
            correct_count=int(evidence >= 0.7),
            last_evidence=evidence,
        )
        db.add(row)
        await db.flush()
        return row
    # Recent evidence matters, while one mistake does not erase the full history.
    row.score = round(row.score * 0.75 + evidence * 100 * 0.25, 2)
    row.attempts += 1
    row.correct_count += int(evidence >= 0.7)
    row.last_evidence = evidence
    return row


async def update_review_item(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    question_id: uuid.UUID | None,
    snapshot: dict,
    correct: bool,
    reviewed: bool = False,
    now: datetime | None = None,
) -> ReviewItem | None:
    now = now or datetime.now(UTC)
    item = None
    if question_id is not None:
        item = await db.scalar(
            select(ReviewItem).where(
                ReviewItem.workspace_id == workspace_id,
                ReviewItem.user_id == user_id,
                ReviewItem.question_id == question_id,
            )
        )
    if item is None and correct:
        return None
    if item is None:
        item = ReviewItem(
            workspace_id=workspace_id,
            user_id=user_id,
            question_id=question_id,
            question_snapshot=snapshot,
            topic=topic_from_snapshot(snapshot),
            due_at=now,
            interval_days=0,
            ease_factor=2.5,
            repetitions=0,
            active=True,
        )
        db.add(item)

    item.question_snapshot = snapshot
    item.active = True
    item.last_correct = correct
    if reviewed:
        repetitions, interval, ease = next_review_schedule(
            item.repetitions, item.interval_days, item.ease_factor, correct
        )
        item.repetitions = repetitions
        item.interval_days = interval
        item.ease_factor = ease
        item.due_at = now + timedelta(days=interval)
        item.last_reviewed_at = now
    elif not correct:
        item.due_at = now
    return item


async def mastery_summary(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, limit: int = 8
) -> list[TopicMastery]:
    rows = await db.scalars(
        select(TopicMastery)
        .where(TopicMastery.workspace_id == workspace_id, TopicMastery.user_id == user_id)
        .order_by(TopicMastery.score, TopicMastery.updated_at.desc())
        .limit(limit)
    )
    return list(rows)


async def adjust_plan_for_weak_topics(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    weak_topics: set[str],
) -> StudyPlan | None:
    """Create a new plan version with an immediate targeted-review stage."""
    if not weak_topics:
        return None
    current = await db.scalar(
        select(StudyPlan)
        .options(selectinload(StudyPlan.stages))
        .where(StudyPlan.workspace_id == workspace_id, StudyPlan.user_id == user_id)
        .order_by(StudyPlan.created_at.desc())
        .limit(1)
    )
    if current is None:
        return None
    ordered_topics = sorted(weak_topics)
    revised = StudyPlan(
        workspace_id=workspace_id,
        user_id=user_id,
        goal=current.goal,
        daily_minutes=current.daily_minutes,
        deadline=current.deadline,
        stages=[],
    )
    db.add(revised)
    await db.flush()
    review = PlanStage(
        plan_id=revised.id,
        position=0,
        title=f"Targeted review: {', '.join(ordered_topics)[:170]}",
        description=(
            "Revisit these concepts because the latest assessment showed weak evidence. "
            "Review the cited material, ask a focused question, then complete another quiz."
        ),
        topics=ordered_topics,
        activities=["read", "chat", "quiz"],
        estimated_minutes=current.daily_minutes,
        status="pending",
    )
    db.add(review)
    revised.stages.append(review)
    for position, stage in enumerate(current.stages, 1):
        copied = PlanStage(
            plan_id=revised.id,
            position=position,
            title=stage.title,
            description=stage.description,
            topics=stage.topics,
            activities=stage.activities,
            estimated_minutes=stage.estimated_minutes,
            status=stage.status,
        )
        db.add(copied)
        revised.stages.append(copied)
    return revised
