"""phase 4 durable lecture sessions

Revision ID: 7ad3c812fe04
Revises: 0b8f82d1c3a9
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7ad3c812fe04"
down_revision: str | Sequence[str] | None = "0b8f82d1c3a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lecture_sessions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("chat_session_id", sa.UUID(), nullable=False),
        sa.Column("plan_stage_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("current_section_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "outline",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "pending_check", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "section_history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["chat_session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_stage_id"], ["plan_stages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lecture_sessions_workspace_id", "lecture_sessions", ["workspace_id"])
    op.create_index("ix_lecture_sessions_user_id", "lecture_sessions", ["user_id"])
    op.create_index("ix_lecture_sessions_chat_session_id", "lecture_sessions", ["chat_session_id"])
    op.create_index("ix_lecture_sessions_plan_stage_id", "lecture_sessions", ["plan_stage_id"])
    op.create_index("ix_lecture_sessions_status", "lecture_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_lecture_sessions_status", table_name="lecture_sessions")
    op.drop_index("ix_lecture_sessions_plan_stage_id", table_name="lecture_sessions")
    op.drop_index("ix_lecture_sessions_chat_session_id", table_name="lecture_sessions")
    op.drop_index("ix_lecture_sessions_user_id", table_name="lecture_sessions")
    op.drop_index("ix_lecture_sessions_workspace_id", table_name="lecture_sessions")
    op.drop_table("lecture_sessions")
