"""long-term user memory

Revision ID: c38a7d9e41f2
Revises: b72f6e2c9d10
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "c38a7d9e41f2"
down_revision: str | Sequence[str] | None = "b72f6e2c9d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages", sa.Column("memory_processed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("messages", sa.Column("memory_processing_error", sa.Text(), nullable=True))
    op.create_table(
        "user_memories",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source_workspace_id", sa.UUID(), nullable=True),
        sa.Column("source_session_id", sa.UUID(), nullable=True),
        sa.Column("source_message_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("memory_key", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("user_confirmed", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1536), nullable=False),
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
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["chat_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_workspace_id"], ["workspaces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "memory_key", name="uq_user_memories_user_key"),
    )
    op.create_index("ix_user_memories_expires_at", "user_memories", ["expires_at"])
    op.create_index(
        "ix_user_memories_source_workspace_id", "user_memories", ["source_workspace_id"]
    )
    op.create_index("ix_user_memories_user_id", "user_memories", ["user_id"])
    op.create_index(
        "ix_user_memories_embedding_hnsw",
        "user_memories",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_user_memories_embedding_hnsw", table_name="user_memories")
    op.drop_index("ix_user_memories_user_id", table_name="user_memories")
    op.drop_index("ix_user_memories_source_workspace_id", table_name="user_memories")
    op.drop_index("ix_user_memories_expires_at", table_name="user_memories")
    op.drop_table("user_memories")
    op.drop_column("messages", "memory_processing_error")
    op.drop_column("messages", "memory_processed_at")
