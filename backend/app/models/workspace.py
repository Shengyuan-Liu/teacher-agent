import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class WorkspaceStatus(enum.StrEnum):
    EMPTY = "empty"
    INGESTING = "ingesting"
    READY = "ready"
    PARTIAL = "partial"


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Scope for Q&A, study plans, quizzes and lectures; holds one or more sources."""

    __tablename__ = "workspaces"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(10), default="zh-CN", nullable=False)
    status: Mapped[WorkspaceStatus] = mapped_column(default=WorkspaceStatus.EMPTY, nullable=False)
    # Topic tree built after ingestion; study plans and lectures are derived from it.
    outline_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    owner: Mapped["User"] = relationship(back_populates="workspaces")
