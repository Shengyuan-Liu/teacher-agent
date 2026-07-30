"""Imported by Alembic so autogenerate can discover every table."""

from app.models.assessment import (
    Assessment,
    AssessmentAnswer,
    AssessmentQuestion,
    ReviewItem,
    TopicMastery,
)
from app.models.chat import ChatSession, Message
from app.models.chunk import Chunk, ChunkParent
from app.models.lecture import LectureSession
from app.models.source import Source, SourceProvenance, SourceStatus, SourceType
from app.models.study_plan import PlanStage, Question, StudyPlan
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus

__all__ = [
    "ChatSession",
    "Assessment",
    "AssessmentAnswer",
    "AssessmentQuestion",
    "Chunk",
    "ChunkParent",
    "Message",
    "LectureSession",
    "PlanStage",
    "Question",
    "ReviewItem",
    "Source",
    "SourceProvenance",
    "SourceStatus",
    "SourceType",
    "StudyPlan",
    "TopicMastery",
    "User",
    "Workspace",
    "WorkspaceStatus",
]
