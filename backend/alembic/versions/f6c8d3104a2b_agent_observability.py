"""Agent observability and replay

Revision ID: f6c8d3104a2b
Revises: e4b7a9012cde
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6c8d3104a2b"
down_revision: str | Sequence[str] | None = "e4b7a9012cde"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("input_message_id", sa.UUID(), nullable=True),
        sa.Column("output_message_id", sa.UUID(), nullable=True),
        sa.Column("replay_of_id", sa.UUID(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("root_span_id", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("intent", sa.String(length=40), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["input_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["output_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["replay_of_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_intent", "agent_runs", ["intent"])
    op.create_index("ix_agent_runs_replay_of_id", "agent_runs", ["replay_of_id"])
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_trace_id", "agent_runs", ["trace_id"], unique=True)
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_workspace_id", "agent_runs", ["workspace_id"])

    op.create_table(
        "agent_spans",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_span_id", sa.String(length=16), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("agent", sa.String(length=80), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("tier", sa.String(length=20), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=20), nullable=True),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_spans_agent", "agent_spans", ["agent"])
    op.create_index("ix_agent_spans_model", "agent_spans", ["model"])
    op.create_index("ix_agent_spans_provider", "agent_spans", ["provider"])
    op.create_index("ix_agent_spans_run_id", "agent_spans", ["run_id"])
    op.create_index("ix_agent_spans_span_id", "agent_spans", ["span_id"])
    op.create_index("ix_agent_spans_stage", "agent_spans", ["stage"])
    op.create_index("ix_agent_spans_trace_id", "agent_spans", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_spans_trace_id", table_name="agent_spans")
    op.drop_index("ix_agent_spans_stage", table_name="agent_spans")
    op.drop_index("ix_agent_spans_span_id", table_name="agent_spans")
    op.drop_index("ix_agent_spans_run_id", table_name="agent_spans")
    op.drop_index("ix_agent_spans_provider", table_name="agent_spans")
    op.drop_index("ix_agent_spans_model", table_name="agent_spans")
    op.drop_index("ix_agent_spans_agent", table_name="agent_spans")
    op.drop_table("agent_spans")
    op.drop_index("ix_agent_runs_workspace_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_trace_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_replay_of_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_intent", table_name="agent_runs")
    op.drop_table("agent_runs")
