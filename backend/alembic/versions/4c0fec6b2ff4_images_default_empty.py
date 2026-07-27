"""images default empty

Revision ID: 4c0fec6b2ff4
Revises: d0f74d9a34e8
Create Date: 2026-07-27 09:56:46.553266

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c0fec6b2ff4'
down_revision: Union[str, Sequence[str], None] = 'd0f74d9a34e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate cannot see this: JSONB stores Python None as a JSON null
    # scalar, which breaks jsonb_array_length and IS NOT NULL filters.
    op.execute("UPDATE chunk_parents SET images = '[]'::jsonb "
               "WHERE images IS NULL OR jsonb_typeof(images) = 'null'")
    op.alter_column("chunk_parents", "images", nullable=False,
                    server_default=sa.text("'[]'::jsonb"))


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("chunk_parents", "images", nullable=True, server_default=None)
