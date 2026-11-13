"""Request and response models for decisions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import DecisionAction, DecisionOutcome, FailureCategory


class DecisionCreate(BaseModel):
    """An action to store, including the keys a later workflow run is matched on."""

    failure_id: int = Field(ge=1)
    action: DecisionAction
    category: FailureCategory
    classifier_confidence: float | None = Field(default=None, ge=0, le=1)
    agreement_score: float | None = Field(default=None, ge=0, le=1)
    reason: str
    thresholds_snapshot: dict = Field(default_factory=dict)
    dry_run: bool = True
    repo: str = Field(min_length=1, max_length=255)
    run_id: int = Field(ge=1)
    # The run_attempt a re-run will come back as. Required for RETRY, empty otherwise.
    expected_run_attempt: int | None = Field(default=None, ge=1)
    # The ai-fix branch CI will run on. Required for FIX, empty otherwise.
    fix_branch: str | None = Field(default=None, max_length=255)
    pr_url: str | None = None
    issue_url: str | None = None
    rerun_requested: bool = False
    outcome: DecisionOutcome = DecisionOutcome.PENDING


class DecisionRead(BaseModel):
    """A stored decision."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    failure_id: int
    action: DecisionAction
    category: FailureCategory
    classifier_confidence: float | None
    agreement_score: float | None
    reason: str
    thresholds_snapshot: dict
    dry_run: bool
    repo: str
    run_id: int
    expected_run_attempt: int | None
    fix_branch: str | None
    pr_url: str | None
    issue_url: str | None
    rerun_requested: bool
    outcome: DecisionOutcome
    created_at: datetime
    resolved_at: datetime | None
