"""Prompt registry and immutable workspace versions

Revision ID: a91d5e7c42bf
Revises: f6c8d3104a2b
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a91d5e7c42bf"
down_revision: str | Sequence[str] | None = "f6c8d3104a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_definitions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("version > 0", name="ck_prompt_version_positive"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_prompt_version_status",
        ),
        sa.UniqueConstraint(
            "workspace_id", "key", name="uq_prompt_definition_workspace_key"
        ),
    )
    op.create_index(
        "ix_prompt_definitions_key", "prompt_definitions", ["key"]
    )
    op.create_index(
        "ix_prompt_definitions_workspace_id",
        "prompt_definitions",
        ["workspace_id"],
    )

    op.create_table(
        "prompt_versions",
        sa.Column("definition_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["prompt_definitions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "definition_id", "content_hash", name="uq_prompt_version_content"
        ),
        sa.UniqueConstraint(
            "definition_id", "version", name="uq_prompt_version_number"
        ),
    )
    op.create_index(
        "ix_prompt_versions_content_hash",
        "prompt_versions",
        ["content_hash"],
    )
    op.create_index(
        "ix_prompt_versions_definition_id",
        "prompt_versions",
        ["definition_id"],
    )
    op.create_index(
        "ix_prompt_versions_status", "prompt_versions", ["status"]
    )
    op.create_index(
        "uq_prompt_active_per_definition",
        "prompt_versions",
        ["definition_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_prompt_active_per_definition", table_name="prompt_versions"
    )
    op.drop_index(
        "ix_prompt_versions_status", table_name="prompt_versions"
    )
    op.drop_index(
        "ix_prompt_versions_definition_id", table_name="prompt_versions"
    )
    op.drop_index(
        "ix_prompt_versions_content_hash", table_name="prompt_versions"
    )
    op.drop_table("prompt_versions")
    op.drop_index(
        "ix_prompt_definitions_workspace_id", table_name="prompt_definitions"
    )
    op.drop_index(
        "ix_prompt_definitions_key", table_name="prompt_definitions"
    )
    op.drop_table("prompt_definitions")
