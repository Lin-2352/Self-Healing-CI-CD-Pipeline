"""Repository behaviour on in-memory SQLite."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.core.enums import (
    DecisionAction,
    DecisionOutcome,
    FailureCategory,
    FailureSource,
    ProcessingStatus,
)
from app.db.errors import InvalidStatusTransition
from app.db.models import Classification, Decision, Failure, FixCandidate
from app.db.repositories import (
    ClassificationRepository,
    DecisionRepository,
    FailureRepository,
    FixCandidateRepository,
)
from app.db.session import make_engine
from app.schemas.classification import ClassificationCreate
from app.schemas.decision import DecisionCreate
from app.schemas.failure import FailureCreate, FailureDetail, FailureRead
from app.schemas.fix import FixCandidateCreate


def _failure(**overrides: object) -> FailureCreate:
    data: dict[str, object] = {
        "repo": "acme/demo",
        "run_id": 100,
        "run_attempt": 1,
        "branch": "main",
        "commit_sha": "abc1234def5678",
        "actor": "dev",
        "workflow_name": "ci",
        "failure_kind_hint": "code",
        "attempt": 0,
        "source": FailureSource.GITHUB,
    }
    data.update(overrides)
    return FailureCreate(**data)  # type: ignore[arg-type]


def _decision(failure_id: int, **overrides: object) -> DecisionCreate:
    data: dict[str, object] = {
        "failure_id": failure_id,
        "action": DecisionAction.ESCALATE,
        "category": FailureCategory.UNKNOWN,
        "reason": "needs a human",
        "thresholds_snapshot": {"classify": 0.75},
        "dry_run": True,
        "repo": "acme/demo",
        "run_id": 100,
    }
    data.update(overrides)
    return DecisionCreate(**data)  # type: ignore[arg-type]


def test_get_or_create_returns_the_existing_row(db_session: Session) -> None:
    repo = FailureRepository(db_session)

    first, created = repo.get_or_create(_failure())
    again, created_again = repo.get_or_create(_failure(actor="someone-else"))
    other, other_created = repo.get_or_create(_failure(run_attempt=2))

    assert created is True
    assert created_again is False
    assert again.id == first.id
    assert again.actor == "dev"
    assert other_created is True
    assert other.id != first.id


def test_get_or_create_keeps_the_session_usable_after_a_lost_race(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The insert that loses a unique-key race must not abort the caller's transaction."""
    repo = FailureRepository(db_session)
    first, _ = repo.get_or_create(_failure())
    real_find = repo._find_by_run
    calls = {"n": 0}

    def miss_once(repo_name: str, run_id: int, run_attempt: int) -> Failure | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_find(repo_name, run_id, run_attempt)

    monkeypatch.setattr(repo, "_find_by_run", miss_once)

    row, created = repo.get_or_create(_failure())

    assert created is False
    assert row.id == first.id
    assert repo.get(first.id) is not None


def test_status_transitions_record_timestamps_and_reject_illegal_moves(db_session: Session) -> None:
    repo = FailureRepository(db_session)
    failure, _ = repo.get_or_create(_failure())

    with pytest.raises(InvalidStatusTransition):
        repo.transition_status(failure, ProcessingStatus.DONE)
    assert failure.processing_status is ProcessingStatus.QUEUED

    repo.transition_status(failure, ProcessingStatus.PROCESSING)
    assert failure.processing_started_at is not None
    assert failure.processing_started_at.tzinfo is not None

    with pytest.raises(ValueError, match="error transitions require a message"):
        repo.transition_status(failure, ProcessingStatus.ERROR, error="   ")

    repo.transition_status(failure, ProcessingStatus.ERROR, error=" llm timed out ")
    assert failure.processing_status is ProcessingStatus.ERROR
    assert failure.processing_error == "llm timed out"
    assert failure.processing_finished_at is not None

    repo.transition_status(failure, ProcessingStatus.QUEUED)
    assert failure.processing_error is None
    assert failure.processing_started_at is None
    assert failure.processing_finished_at is None

    repo.transition_status(failure, ProcessingStatus.PROCESSING)
    repo.transition_status(failure, ProcessingStatus.DONE)
    assert failure.processing_status is ProcessingStatus.DONE
    with pytest.raises(InvalidStatusTransition):
        repo.transition_status(failure, ProcessingStatus.QUEUED)
    repo.transition_status(failure, ProcessingStatus.QUEUED, force=True)
    assert failure.processing_status is ProcessingStatus.QUEUED


def test_created_at_is_timezone_aware_utc(db_session: Session) -> None:
    repo = FailureRepository(db_session)
    failure, _ = repo.get_or_create(_failure())
    db_session.commit()
    db_session.refresh(failure)

    assert failure.created_at.utcoffset() == timedelta(0)


def test_deleting_a_failure_cascades_to_its_children(db_session: Session) -> None:
    failures = FailureRepository(db_session)
    failure, _ = failures.get_or_create(_failure())
    kept, _ = failures.get_or_create(_failure(run_id=200))

    classification = ClassificationRepository(db_session).add(
        ClassificationCreate(
            failure_id=failure.id,
            category=FailureCategory.TEST_FAILURE,
            confidence=0.9,
            reasoning="assertion failed",
            model="mock",
            raw_output="{}",
        )
    )
    candidate = FixCandidateRepository(db_session).add(
        FixCandidateCreate(
            failure_id=failure.id,
            candidate_index=0,
            file_path="app.py",
            edits="<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE",
            applied_ok=True,
        )
    )
    decision = DecisionRepository(db_session).add(
        _decision(failure.id, action=DecisionAction.ESCALATE)
    )
    kept_decision = DecisionRepository(db_session).add(_decision(kept.id, run_id=200))
    db_session.commit()
    gone = (classification.id, candidate.id, decision.id)
    kept_decision_id = kept_decision.id

    # A bulk DELETE bypasses the ORM, so this only passes when the database itself cascades.
    assert db_session.scalar(text("PRAGMA foreign_keys")) == 1
    db_session.execute(delete(Failure).where(Failure.id == failure.id))
    db_session.commit()
    # Drop the identity map: a cached row would hide whether the database deleted it.
    db_session.expunge_all()

    classification_id, candidate_id, decision_id = gone
    assert db_session.get(Classification, classification_id) is None
    assert db_session.get(FixCandidate, candidate_id) is None
    assert db_session.get(Decision, decision_id) is None
    assert db_session.get(Decision, kept_decision_id) is not None


def test_list_failures_filters_and_paginates(db_session: Session) -> None:
    repo = FailureRepository(db_session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(3):
        row, _ = repo.get_or_create(_failure(run_attempt=index + 1))
        row.created_at = base + timedelta(minutes=index)
    other, _ = repo.get_or_create(_failure(repo="acme/other", run_attempt=1))
    other.created_at = base + timedelta(hours=1)
    repo.transition_status(other, ProcessingStatus.PROCESSING)
    db_session.commit()

    page, total = repo.list_failures(repo="acme/demo", limit=2, offset=0)
    rest, _ = repo.list_failures(repo="acme/demo", limit=2, offset=2)
    processing, processing_total = repo.list_failures(processing_status=ProcessingStatus.PROCESSING)

    assert total == 3
    assert [row.run_attempt for row in page] == [3, 2]
    assert [row.run_attempt for row in rest] == [1]
    assert processing_total == 1
    assert processing[0].repo == "acme/other"
    with pytest.raises(ValueError):
        repo.list_failures(limit=0)
    with pytest.raises(ValueError):
        repo.list_failures(offset=-1)


def test_decisions_can_be_linked_back_from_a_later_run(db_session: Session) -> None:
    failures = FailureRepository(db_session)
    failure, _ = failures.get_or_create(_failure())
    decisions = DecisionRepository(db_session)

    retry = decisions.add(
        _decision(
            failure.id,
            action=DecisionAction.RETRY,
            category=FailureCategory.FLAKY_OR_NETWORK,
            expected_run_attempt=2,
            rerun_requested=True,
            thresholds_snapshot={"classify": 0.75, "retries": {"max": 1}},
        )
    )
    fix = decisions.add(
        _decision(
            failure.id,
            action=DecisionAction.FIX,
            category=FailureCategory.TEST_FAILURE,
            fix_branch="ai-fix-assert-abc1234",
            agreement_score=0.8,
        )
    )
    db_session.commit()
    db_session.refresh(retry)

    assert decisions.find_open_retry("acme/demo", 100, 2).id == retry.id
    assert decisions.find_open_retry("acme/demo", 100, 1) is None
    assert decisions.find_open_fix("acme/demo", "ai-fix-assert-abc1234").id == fix.id
    assert decisions.find_open_fix("acme/demo", "ai-fix-other") is None
    assert retry.thresholds_snapshot == {"classify": 0.75, "retries": {"max": 1}}
    assert retry.created_at.tzinfo is not None

    retry.outcome = DecisionOutcome.RETRY_SUCCESS
    db_session.commit()
    assert decisions.find_open_retry("acme/demo", 100, 2) is None


def test_retry_and_fix_require_their_link_fields(db_session: Session) -> None:
    failure, _ = FailureRepository(db_session).get_or_create(_failure())
    decisions = DecisionRepository(db_session)

    with pytest.raises(ValueError, match="expected_run_attempt"):
        decisions.add(_decision(failure.id, action=DecisionAction.RETRY))
    with pytest.raises(ValueError, match="fix_branch"):
        decisions.add(_decision(failure.id, action=DecisionAction.FIX, fix_branch=""))


def test_list_decisions_filters(db_session: Session) -> None:
    failure, _ = FailureRepository(db_session).get_or_create(_failure())
    decisions = DecisionRepository(db_session)
    decisions.add(_decision(failure.id, action=DecisionAction.ESCALATE))
    decisions.add(
        _decision(
            failure.id,
            action=DecisionAction.RETRY,
            category=FailureCategory.FLAKY_OR_NETWORK,
            expected_run_attempt=2,
        )
    )

    retries, total = decisions.list_decisions(action=DecisionAction.RETRY)
    flaky, _ = decisions.list_decisions(category=FailureCategory.FLAKY_OR_NETWORK)
    pending, pending_total = decisions.list_decisions(outcome=DecisionOutcome.PENDING)

    assert total == 1
    assert retries[0].action is DecisionAction.RETRY
    assert len(flaky) == 1
    assert pending_total == 2


def test_read_schemas_round_trip_a_failure(db_session: Session) -> None:
    failure, _ = FailureRepository(db_session).get_or_create(_failure())
    ClassificationRepository(db_session).add(
        ClassificationCreate(
            failure_id=failure.id,
            category=FailureCategory.DEPENDENCY_ERROR,
            confidence=0.8,
            reasoning="missing package",
            model="mock",
            raw_output="{}",
        )
    )
    db_session.commit()
    db_session.refresh(failure)

    detail = FailureDetail.model_validate(failure)

    assert isinstance(FailureRead.model_validate(failure), FailureRead)
    assert detail.classifications[0].category is FailureCategory.DEPENDENCY_ERROR
    assert detail.fix_candidates == []
    assert detail.created_at.tzinfo is not None
    assert detail.ground_truth_category is None
    assert detail.recovery_count == 0


def test_fix_attempts_count_only_the_streak_since_verified_success(db_session: Session) -> None:
    """A verified fix resets the heal budget for that repository and base branch."""
    failures = FailureRepository(db_session)
    decisions = DecisionRepository(db_session)
    healed, _ = failures.get_or_create(_failure(run_id=1))
    failed, _ = failures.get_or_create(_failure(run_id=2))
    still_open, _ = failures.get_or_create(_failure(run_id=3))
    other_branch, _ = failures.get_or_create(_failure(run_id=4, branch="dev"))
    decisions.add(
        _decision(
            healed.id,
            action=DecisionAction.FIX,
            fix_branch="ai-fix-a",
            outcome=DecisionOutcome.VERIFIED_SUCCESS,
        )
    )
    decisions.add(
        _decision(
            failed.id,
            action=DecisionAction.FIX,
            fix_branch="ai-fix-b",
            outcome=DecisionOutcome.VERIFIED_FAILURE,
        )
    )
    decisions.add(_decision(still_open.id, action=DecisionAction.FIX, fix_branch="ai-fix-c"))
    decisions.add(_decision(other_branch.id, action=DecisionAction.FIX, fix_branch="ai-fix-d"))

    # The verified row is the reset point, so only the two later main-branch fixes count.
    assert decisions.count_fix_attempts("acme/demo", "main") == 2
    assert decisions.count_fix_attempts("acme/demo", "dev") == 1
    assert decisions.count_fix_attempts("other/demo", "main") == 0


def test_recover_reschedules_until_the_cap(db_session: Session) -> None:
    """Queued and stale rows are returned. A live processing row and an over-cap row are not."""
    repo = FailureRepository(db_session)
    queued, _ = repo.get_or_create(_failure(run_id=1))
    stale, _ = repo.get_or_create(_failure(run_id=2))
    fresh, _ = repo.get_or_create(_failure(run_id=3))
    exhausted, _ = repo.get_or_create(_failure(run_id=4))
    repo.transition_status(stale, ProcessingStatus.PROCESSING)
    repo.transition_status(fresh, ProcessingStatus.PROCESSING)
    stale.processing_started_at = datetime.now(UTC) - timedelta(hours=3)
    exhausted.recovery_count = 2

    scheduled = repo.recover(datetime.now(UTC) - timedelta(seconds=30), max_recoveries=2)

    assert set(scheduled) == {queued.id, stale.id}
    assert queued.recovery_count == 1
    assert queued.processing_status is ProcessingStatus.QUEUED
    assert stale.processing_status is ProcessingStatus.QUEUED
    assert stale.recovery_count == 1
    assert fresh.processing_status is ProcessingStatus.PROCESSING
    assert fresh.recovery_count == 0
    assert exhausted.processing_status is ProcessingStatus.ERROR
    assert exhausted.processing_error == "recovery limit reached"


def test_sqlite_file_enables_wal_and_a_busy_timeout(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'ci.db').as_posix()}"
    engine = make_engine(url)
    with engine.connect() as connection:
        mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
    engine.dispose()

    assert mode == "wal"
    assert timeout == 5000
