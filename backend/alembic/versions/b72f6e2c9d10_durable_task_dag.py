"""durable typed task dag checkpoints

Revision ID: b72f6e2c9d10
Revises: a91d5e7c42bf
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b72f6e2c9d10"
down_revision: str | Sequence[str] | None = "a91d5e7c42bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_executions",
        sa.Column("execution_key", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("dag_hash", sa.String(length=64), nullable=False),
        sa.Column("dag", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resume_count", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_executions_execution_key", "task_executions", ["execution_key"], unique=True
    )
    op.create_index(
        "ix_task_executions_lease_expires_at", "task_executions", ["lease_expires_at"]
    )
    op.create_index("ix_task_executions_lease_owner", "task_executions", ["lease_owner"])
    op.create_index("ix_task_executions_session_id", "task_executions", ["session_id"])
    op.create_index("ix_task_executions_status", "task_executions", ["status"])
    op.create_index("ix_task_executions_workspace_id", "task_executions", ["workspace_id"])

    op.create_table(
        "task_node_checkpoints",
        sa.Column("execution_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("agent", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["execution_id"], ["task_executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id", "task_id", name="uq_task_checkpoint_execution_node"
        ),
    )
    op.create_index(
        "ix_task_node_checkpoints_execution_id",
        "task_node_checkpoints",
        ["execution_id"],
    )
    op.create_index(
        "ix_task_node_checkpoints_status", "task_node_checkpoints", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_node_checkpoints_status", table_name="task_node_checkpoints")
    op.drop_index(
        "ix_task_node_checkpoints_execution_id", table_name="task_node_checkpoints"
    )
    op.drop_table("task_node_checkpoints")
    op.drop_index("ix_task_executions_workspace_id", table_name="task_executions")
    op.drop_index("ix_task_executions_status", table_name="task_executions")
    op.drop_index("ix_task_executions_session_id", table_name="task_executions")
    op.drop_index("ix_task_executions_lease_owner", table_name="task_executions")
    op.drop_index("ix_task_executions_lease_expires_at", table_name="task_executions")
    op.drop_index("ix_task_executions_execution_key", table_name="task_executions")
    op.drop_table("task_executions")
