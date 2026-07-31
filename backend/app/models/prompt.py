"""Workspace prompt overrides with immutable, activatable versions."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class PromptDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace-owned stable prompt key whose content lives in versions."""

    __tablename__ = "prompt_definitions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", name="uq_prompt_definition_workspace_key"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    workspace: Mapped["Workspace"] = relationship()
    created_by: Mapped["User"] = relationship()
    versions: Mapped[list["PromptVersion"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PromptVersion.version.desc()",
    )


class PromptVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable prompt body; lifecycle changes status, never template content."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_prompt_version_number"),
        UniqueConstraint("definition_id", "content_hash", name="uq_prompt_version_content"),
        CheckConstraint("version > 0", name="ck_prompt_version_positive"),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_prompt_version_status",
        ),
        Index(
            "uq_prompt_active_per_definition",
            "definition_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompt_definitions.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    definition: Mapped["PromptDefinition"] = relationship(back_populates="versions")
    created_by: Mapped["User"] = relationship()
