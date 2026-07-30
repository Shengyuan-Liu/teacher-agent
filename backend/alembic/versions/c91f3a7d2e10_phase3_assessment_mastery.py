"""phase 3 assessment, review and mastery

Revision ID: c91f3a7d2e10
Revises: a0b63456e6d5
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c91f3a7d2e10"
down_revision: str | Sequence[str] | None = "a0b63456e6d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("time_limit_minutes", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assessments_user_id", "assessments", ["user_id"])
    op.create_index("ix_assessments_workspace_id", "assessments", ["workspace_id"])

    op.create_table(
        "assessment_questions",
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("question_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_questions_assessment_id", "assessment_questions", ["assessment_id"]
    )
    op.create_index("ix_assessment_questions_question_id", "assessment_questions", ["question_id"])

    op.create_table(
        "assessment_answers",
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score_fraction", sa.Float(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("grader", sa.String(length=20), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assessment_question_id"], ["assessment_questions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_id", "assessment_question_id"),
    )
    op.create_index("ix_assessment_answers_assessment_id", "assessment_answers", ["assessment_id"])
    op.create_index(
        "ix_assessment_answers_assessment_question_id",
        "assessment_answers",
        ["assessment_question_id"],
    )

    op.create_table(
        "review_items",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("ease_factor", sa.Float(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_correct", sa.Boolean(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", "question_id", name="uq_review_question"),
    )
    op.create_index("ix_review_items_due_at", "review_items", ["due_at"])
    op.create_index("ix_review_items_question_id", "review_items", ["question_id"])
    op.create_index("ix_review_items_topic", "review_items", ["topic"])
    op.create_index("ix_review_items_user_id", "review_items", ["user_id"])
    op.create_index("ix_review_items_workspace_id", "review_items", ["workspace_id"])

    op.create_table(
        "topic_mastery",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=False),
        sa.Column("last_evidence", sa.Float(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", "topic", name="uq_user_topic_mastery"),
    )
    op.create_index("ix_topic_mastery_topic", "topic_mastery", ["topic"])
    op.create_index("ix_topic_mastery_user_id", "topic_mastery", ["user_id"])
    op.create_index("ix_topic_mastery_workspace_id", "topic_mastery", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("topic_mastery")
    op.drop_table("review_items")
    op.drop_table("assessment_answers")
    op.drop_table("assessment_questions")
    op.drop_table("assessments")
