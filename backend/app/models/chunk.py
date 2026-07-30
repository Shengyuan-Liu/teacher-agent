import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.source import Source


class ChunkParent(UUIDPrimaryKeyMixin, Base):
    """Section-sized context. Not embedded; retrieved via its children."""

    __tablename__ = "chunk_parents"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: [{"id": "img-0.jpeg", "path": "storage/..."}] for figures in this section
    images: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)

    source: Mapped["Source"] = relationship(back_populates="chunk_parents")
    children: Mapped[list["Chunk"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(UUIDPrimaryKeyMixin, Base):
    """Passage-sized unit that carries the embedding used for retrieval."""

    __tablename__ = "chunks"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunk_parents.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=False
    )

    parent: Mapped["ChunkParent"] = relationship(back_populates="children")
    source: Mapped["Source"] = relationship(back_populates="chunks")
