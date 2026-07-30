"""store PDF page ranges on parent chunks

Revision ID: 0b8f82d1c3a9
Revises: f7a82c09e614
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0b8f82d1c3a9"
down_revision: str | Sequence[str] | None = "f7a82c09e614"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunk_parents", sa.Column("page_start", sa.Integer(), nullable=True))
    op.add_column("chunk_parents", sa.Column("page_end", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunk_parents", "page_end")
    op.drop_column("chunk_parents", "page_start")
