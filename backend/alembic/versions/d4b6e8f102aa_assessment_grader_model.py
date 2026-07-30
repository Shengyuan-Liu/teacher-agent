"""store the exact model used for subjective grading

Revision ID: d4b6e8f102aa
Revises: c91f3a7d2e10
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b6e8f102aa"
down_revision: str | Sequence[str] | None = "c91f3a7d2e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assessment_answers", sa.Column("grader_model", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("assessment_answers", "grader_model")
