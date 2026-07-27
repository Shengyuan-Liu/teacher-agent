import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.chunk import Chunk, ChunkParent


class SourceType(enum.StrEnum):
    PDF = "pdf"
    MARKDOWN = "md"


class SourceStatus(enum.StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    type: Mapped[SourceType] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[SourceStatus] = mapped_column(default=SourceStatus.PENDING, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )
    chunk_parents: Mapped[list["ChunkParent"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )
