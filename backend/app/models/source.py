import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.chunk import Chunk, ChunkParent


class SourceType(enum.StrEnum):
    PDF = "pdf"
    MARKDOWN = "md"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    URL = "url"
    GITHUB = "github"


class SourceStatus(enum.StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class SourceProvenance(enum.StrEnum):
    """How the source entered the workspace, so the UI can set web results
    apart from the user's own material and filter/delete by channel."""

    USER_UPLOAD = "user_upload"
    USER_URL = "user_url"
    USER_GITHUB = "user_github"
    WEB_SEARCH = "web_search"


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
    #: uploaded snapshot on disk; for url/github sources this is the crawled markdown
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    #: where the content came from (seed URL or repository URL); None for uploads
    origin: Mapped[str | None] = mapped_column(String(1000))
    # SQLAlchemy stores enums by member name, so the DB label is USER_UPLOAD
    # even though the value the app and API speak is "user_upload".
    provenance: Mapped[SourceProvenance] = mapped_column(
        default=SourceProvenance.USER_UPLOAD,
        server_default=SourceProvenance.USER_UPLOAD.name,
        nullable=False,
        index=True,
    )
    #: the query that surfaced this source; only set when provenance=web_search
    search_query: Mapped[str | None] = mapped_column(String(500))
    #: when the page was fetched; matters most for web material's freshness
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SourceStatus] = mapped_column(default=SourceStatus.PENDING, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    #: 0..1 through the ingestion pipeline
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    progress_detail: Mapped[str | None] = mapped_column(String(200))

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )
    chunk_parents: Mapped[list["ChunkParent"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )
