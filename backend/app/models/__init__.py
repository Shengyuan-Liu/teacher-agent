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
from app.models.evaluation import EvalCase, EvalDataset, EvalResult, EvalRun
from app.models.lecture import LectureSession
from app.models.observability import AgentRun, AgentSpan
from app.models.prompt import PromptDefinition, PromptVersion
from app.models.source import Source, SourceProvenance, SourceStatus, SourceType
from app.models.study_plan import PlanStage, Question, StudyPlan
from app.models.task_execution import TaskExecution, TaskNodeCheckpoint
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus

__all__ = [
    "ChatSession",
    "Assessment",
    "AssessmentAnswer",
    "AssessmentQuestion",
    "AgentRun",
    "AgentSpan",
    "Chunk",
    "ChunkParent",
    "EvalCase",
    "EvalDataset",
    "EvalResult",
    "EvalRun",
    "Message",
    "LectureSession",
    "PlanStage",
    "PromptDefinition",
    "PromptVersion",
    "Question",
    "ReviewItem",
    "Source",
    "SourceProvenance",
    "SourceStatus",
    "SourceType",
    "StudyPlan",
    "TaskExecution",
    "TaskNodeCheckpoint",
    "TopicMastery",
    "User",
    "Workspace",
    "WorkspaceStatus",
]
