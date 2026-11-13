"""Request and response models for failures."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import FailureCategory, FailureSource, ProcessingStatus
from app.schemas.classification import ClassificationRead
from app.schemas.decision import DecisionRead
from app.schemas.fix import FixCandidateRead


class FailureCreate(BaseModel):
    """Fields needed to record one failed workflow run."""

    repo: str = Field(min_length=1, max_length=255)
    run_id: int = Field(ge=1)
    run_attempt: int = Field(ge=1)
    branch: str = Field(min_length=1, max_length=255)
    commit_sha: str = Field(min_length=7, max_length=64)
    actor: str | None = None
    workflow_name: str | None = None
    failure_kind_hint: str | None = None
    attempt: int = Field(default=0, ge=0)
    source: FailureSource
    ground_truth_category: FailureCategory | None = None


class FailureRead(BaseModel):
    """A stored failure, as the dashboard and the API return it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    repo: str
    run_id: int
    run_attempt: int
    branch: str
    commit_sha: str
    actor: str | None
    workflow_name: str | None
    failure_kind_hint: str | None
    attempt: int
    source: FailureSource
    processing_status: ProcessingStatus
    processing_error: str | None
    processing_started_at: datetime | None
    processing_finished_at: datetime | None
    redacted_log_excerpt: str | None
    redaction_count: int
    ground_truth_category: FailureCategory | None
    recovery_count: int
    created_at: datetime


class FailureDetail(FailureRead):
    """A failure plus the diagnosis, patches, and decisions recorded against it."""

    classifications: list[ClassificationRead]
    fix_candidates: list[FixCandidateRead]
    decisions: list[DecisionRead]
