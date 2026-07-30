"""web search: source provenance, message web citations

Revision ID: a0b63456e6d5
Revises: 32bc9f69d198
Create Date: 2026-07-29 21:21:18.605816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a0b63456e6d5'
down_revision: Union[str, Sequence[str], None] = '32bc9f69d198'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Autogenerate emits the enum inline on the column, which creates the type as a
# side effect but never drops it on downgrade. Manage it explicitly instead, or
# `downgrade base` then `upgrade head` fails on "type already exists".
# Labels are the member names (uppercase): SQLAlchemy stores enums by name, as
# the existing sourcetype/sourcestatus types already do.
provenance = postgresql.ENUM(
    'USER_UPLOAD', 'USER_URL', 'USER_GITHUB', 'WEB_SEARCH',
    name='sourceprovenance',
)


def upgrade() -> None:
    provenance.create(op.get_bind(), checkfirst=True)
    op.add_column('messages', sa.Column('web_citations', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column('messages', sa.Column('used_web_search', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('sources', sa.Column('provenance', provenance, server_default='USER_UPLOAD', nullable=False))
    op.add_column('sources', sa.Column('search_query', sa.String(length=500), nullable=True))
    op.add_column('sources', sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_sources_provenance'), 'sources', ['provenance'], unique=False)

    # Existing rows all defaulted to USER_UPLOAD; reclassify the remote ones from
    # their type so the "own vs web" filter is right for material added earlier.
    op.execute("UPDATE sources SET provenance = 'USER_URL' WHERE type = 'URL'")
    op.execute("UPDATE sources SET provenance = 'USER_GITHUB' WHERE type = 'GITHUB'")


def downgrade() -> None:
    op.drop_index(op.f('ix_sources_provenance'), table_name='sources')
    op.drop_column('sources', 'fetched_at')
    op.drop_column('sources', 'search_query')
    op.drop_column('sources', 'provenance')
    op.drop_column('messages', 'used_web_search')
    op.drop_column('messages', 'web_citations')
    provenance.drop(op.get_bind(), checkfirst=True)
