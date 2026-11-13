"""Tables that record a failure, how it was diagnosed, and what was done about it.

Generic `sqlalchemy.JSON` is used (not PostgreSQL JSONB) so the same models run on SQLite.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    DecisionAction,
    DecisionOutcome,
    FailureCategory,
    FailureSource,
    ProcessingStatus,
)
from app.db.base import Base
from app.db.types import UTCDateTime, utcnow

_NAME_LENGTH = 255


def _enum(enum_type: type) -> Enum:
    """Store an enum as its string value, portable across databases."""
    return Enum(enum_type, native_enum=False, length=32)


class Failure(Base):
    """One failed workflow run, deduplicated on (repo, run_id, run_attempt)."""

    __tablename__ = "failures"
    __table_args__ = (
        UniqueConstraint("repo", "run_id", "run_attempt", name="uq_failures_repo_run"),
        Index("ix_failures_repo_branch", "repo", "branch"),
        Index("ix_failures_ground_truth", "ground_truth_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String(_NAME_LENGTH), index=True)
    run_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(Integer)
    branch: Mapped[str] = mapped_column(String(_NAME_LENGTH))
    commit_sha: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str | None] = mapped_column(String(255))
    workflow_name: Mapped[str | None] = mapped_column(String(255))
    failure_kind_hint: Mapped[str | None] = mapped_column(String(64))
    # Heal attempt copied from the `Heal-Attempt` trailer. 0 means this run was not a heal.
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[FailureSource] = mapped_column(_enum(FailureSource))
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        _enum(ProcessingStatus), default=ProcessingStatus.QUEUED, index=True
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    processing_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    redacted_log_excerpt: Mapped[str | None] = mapped_column(Text)
    redaction_count: Mapped[int] = mapped_column(Integer, default=0)
    # Set by an evaluation run. Empty for live webhook traffic.
    ground_truth_category: Mapped[FailureCategory | None] = mapped_column(
        _enum(FailureCategory), nullable=True
    )
    # How many times startup has put this row back on the queue. Past the cap it is marked error.
    recovery_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    classifications: Mapped[list["Classification"]] = relationship(
        back_populates="failure", cascade="all, delete-orphan"
    )
    fix_candidates: Mapped[list["FixCandidate"]] = relationship(
        back_populates="failure", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["Decision"]] = relationship(
        back_populates="failure", cascade="all, delete-orphan"
    )


class Classification(Base):
    """The classifier's verdict for one failure."""

    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    failure_id: Mapped[int] = mapped_column(
        ForeignKey("failures.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[FailureCategory] = mapped_column(_enum(FailureCategory), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255))
    raw_output: Mapped[str] = mapped_column(Text)
    # One object per sample: category, self-reported confidence, raw output, parse status.
    # The row's own category and confidence are the aggregate, so evaluation can compare them.
    samples: Mapped[list] = mapped_column(JSON, default=list)
    parse_attempts: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    failure: Mapped[Failure] = relationship(back_populates="classifications")


class FixCandidate(Base):
    """One of the N patches generated so their agreement can be measured."""

    __tablename__ = "fix_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    failure_id: Mapped[int] = mapped_column(
        ForeignKey("failures.id", ondelete="CASCADE"), index=True
    )
    candidate_index: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(String(1024))
    edits: Mapped[str] = mapped_column(Text)
    applied_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    failure: Mapped[Failure] = relationship(back_populates="fix_candidates")


class Decision(Base):
    """The action taken for a failure, plus the keys a later workflow run is matched on.

    A RETRY stores `expected_run_attempt` (the run_attempt GitHub will report for the re-run
    of the same `run_id`). A FIX stores `fix_branch` (the `ai-fix-*` branch CI runs on).
    `repo` and `run_id` are copied here so that match does not depend on joining failures.
    """

    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_retry_lookup", "repo", "run_id", "expected_run_attempt"),
        Index("ix_decisions_fix_branch", "fix_branch"),
        Index("ix_decisions_stats", "dry_run", "outcome", "created_at"),
        Index("ix_decisions_action_outcome", "action", "outcome"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    failure_id: Mapped[int] = mapped_column(
        ForeignKey("failures.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[DecisionAction] = mapped_column(_enum(DecisionAction), index=True)
    category: Mapped[FailureCategory] = mapped_column(_enum(FailureCategory), index=True)
    classifier_confidence: Mapped[float | None] = mapped_column(Float)
    agreement_score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    thresholds_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    repo: Mapped[str] = mapped_column(String(255), index=True)
    run_id: Mapped[int] = mapped_column(BigInteger, index=True)
    expected_run_attempt: Mapped[int | None] = mapped_column(Integer)
    fix_branch: Mapped[str | None] = mapped_column(String(255))
    pr_url: Mapped[str | None] = mapped_column(String(512))
    issue_url: Mapped[str | None] = mapped_column(String(512))
    rerun_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[DecisionOutcome] = mapped_column(
        _enum(DecisionOutcome), default=DecisionOutcome.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    failure: Mapped[Failure] = relationship(back_populates="decisions")
