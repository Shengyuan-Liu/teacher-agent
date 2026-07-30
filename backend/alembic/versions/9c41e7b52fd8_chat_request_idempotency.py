"""chat request idempotency

Revision ID: 9c41e7b52fd8
Revises: 7ad3c812fe04
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c41e7b52fd8"
down_revision: str | Sequence[str] | None = "7ad3c812fe04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("client_request_id", sa.UUID(), nullable=True))
    op.create_index(
        "ix_messages_client_request_id",
        "messages",
        ["client_request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_client_request_id", table_name="messages")
    op.drop_column("messages", "client_request_id")
