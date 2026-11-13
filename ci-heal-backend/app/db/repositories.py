"""Read and write helpers. Routers talk to these, not to queries scattered through the app.

Limits are capped so a dashboard request cannot ask the database for an unbounded page.
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import (
    DecisionAction,
    DecisionOutcome,
    FailureCategory,
    ProcessingStatus,
)
from app.db.errors import InvalidStatusTransition
from app.db.models import Classification, Decision, Failure, FixCandidate
from app.db.types import utcnow
from app.schemas.classification import ClassificationCreate
from app.schemas.decision import DecisionCreate
from app.schemas.failure import FailureCreate
from app.schemas.fix import FixCandidateCreate

_MAX_PAGE_SIZE = 200

# queued -> processing -> done|error.
# Back to the queue: an error, a stale processing row, or a done row an operator forces.
# queued -> error is the startup recovery cap.
_TRANSITIONS: dict[ProcessingStatus, set[ProcessingStatus]] = {
    ProcessingStatus.QUEUED: {ProcessingStatus.PROCESSING, ProcessingStatus.ERROR},
    ProcessingStatus.PROCESSING: {
        ProcessingStatus.DONE,
        ProcessingStatus.ERROR,
        ProcessingStatus.QUEUED,
    },
    ProcessingStatus.ERROR: {ProcessingStatus.QUEUED},
    ProcessingStatus.DONE: set(),
}


def _bounded_limit(limit: int) -> int:
    """Reject a non-positive page size and cap a large one."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return min(limit, _MAX_PAGE_SIZE)


def _bounded_offset(offset: int) -> int:
    """Reject a negative offset."""
    if offset < 0:
        raise ValueError("offset must be >= 0")
    return offset


class FailureRepository:
    """Persistence for failed workflow runs."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to one session. The caller owns commit and rollback."""
        self._session = session

    def get(self, failure_id: int) -> Failure | None:
        """Return one failure by its primary key, or None."""
        return self._session.get(Failure, failure_id)

    def get_or_create(self, data: FailureCreate) -> tuple[Failure, bool]:
        """Insert a failure, or return the row that already occupies its run.

        The insert happens inside a SAVEPOINT. If two requests record the same
        (repo, run_id, run_attempt) at once, the loser rolls back only the insert
        and returns the winner's row; the surrounding transaction stays usable.

        Returns:
            The row, and True when this call inserted it.
        """
        existing = self._find_by_run(data.repo, data.run_id, data.run_attempt)
        if existing is not None:
            return existing, False

        row = Failure(**data.model_dump())
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError:
            existing = self._find_by_run(data.repo, data.run_id, data.run_attempt)
            if existing is None:
                raise
            return existing, False
        return row, True

    def transition_status(
        self,
        failure: Failure,
        status: ProcessingStatus,
        error: str | None = None,
        *,
        force: bool = False,
    ) -> Failure:
        """Move a failure to `status`, recording when processing started or finished.

        `force` allows a finished row back onto the queue. That is the operator
        `reprocess?force=true` path. A row that is processing is never forced.

        Raises:
            InvalidStatusTransition: The state machine does not allow this move.
            ValueError: An error transition was requested without a message.
        """
        current = ProcessingStatus(failure.processing_status)
        target = ProcessingStatus(status)
        message = (error or "").strip()
        forced_requeue = (
            force and current is ProcessingStatus.DONE and target is ProcessingStatus.QUEUED
        )
        if target not in _TRANSITIONS[current] and not forced_requeue:
            raise InvalidStatusTransition(f"{current.value} -> {target.value} is not allowed")
        if target is ProcessingStatus.ERROR and not message:
            raise ValueError("error transitions require a message")

        now = utcnow()
        failure.processing_status = target
        if target is ProcessingStatus.PROCESSING:
            failure.processing_started_at = now
            failure.processing_finished_at = None
            failure.processing_error = None
        elif target is ProcessingStatus.DONE:
            failure.processing_finished_at = now
            failure.processing_error = None
        elif target is ProcessingStatus.ERROR:
            failure.processing_finished_at = now
            failure.processing_error = message
        else:
            # Back to the queue: drop the previous attempt's timestamps and message.
            failure.processing_started_at = None
            failure.processing_finished_at = None
            failure.processing_error = None
        self._session.flush()
        return failure

    def list_failures(
        self,
        *,
        repo: str | None = None,
        processing_status: ProcessingStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Failure], int]:
        """Return a page of failures, newest first, and the total matching the filters."""
        limit = _bounded_limit(limit)
        offset = _bounded_offset(offset)
        filters = []
        if repo is not None:
            filters.append(Failure.repo == repo)
        if processing_status is not None:
            filters.append(Failure.processing_status == processing_status)

        total = self._session.scalar(select(func.count()).select_from(Failure).where(*filters))
        rows = self._session.scalars(
            select(Failure)
            .where(*filters)
            .order_by(Failure.created_at.desc(), Failure.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return list(rows), int(total or 0)

    def claim(self, failure_id: int) -> Failure | None:
        """Move one queued failure to processing, or return None if another worker took it.

        The update matches only a row that is still queued, so two sessions cannot both
        claim it.

        Args:
            failure_id: The failure to take.

        Returns:
            The claimed row, or None when it was not queued.
        """
        claimed = self._session.scalar(
            update(Failure)
            .where(
                Failure.id == failure_id,
                Failure.processing_status == ProcessingStatus.QUEUED,
            )
            .values(
                processing_status=ProcessingStatus.PROCESSING,
                processing_started_at=utcnow(),
                processing_finished_at=None,
                processing_error=None,
            )
            .returning(Failure.id)
        )
        if claimed is None:
            return None
        self._session.flush()
        self._session.expire_all()
        return self._session.get(Failure, failure_id)

    def recover(self, cutoff: datetime, max_recoveries: int) -> list[int]:
        """Requeue work that startup should run again, and stop rows past the cap.

        Queued rows are included. A processing row is included when it has no start
        time or the start is older than `cutoff`. Each included row increments
        `recovery_count`. A row whose count is already at `max_recoveries` is marked
        error and is not returned, so a crash loop stops.

        The age comparison happens in Python so a SQLite timestamp and a timezone-aware
        cutoff still agree.

        Args:
            cutoff: Processing rows that started at or after this stay where they are.
            max_recoveries: How many startup reschedules a row may have.

        Returns:
            Ids that are queued and should be scheduled. Over-cap ids are not included.
        """
        rows = list(
            self._session.scalars(
                select(Failure).where(
                    Failure.processing_status.in_(
                        (ProcessingStatus.QUEUED, ProcessingStatus.PROCESSING)
                    )
                )
            ).all()
        )
        scheduled: list[int] = []
        for row in rows:
            if row.processing_status is ProcessingStatus.PROCESSING:
                started = row.processing_started_at
                if started is not None and started >= cutoff:
                    continue
            if row.recovery_count >= max_recoveries:
                self.transition_status(row, ProcessingStatus.ERROR, error="recovery limit reached")
                continue
            if row.processing_status is ProcessingStatus.PROCESSING:
                self.transition_status(row, ProcessingStatus.QUEUED)
            row.recovery_count += 1
            scheduled.append(row.id)
        return scheduled

    def _find_by_run(self, repo: str, run_id: int, run_attempt: int) -> Failure | None:
        """Look up the single failure for one GitHub run attempt."""
        return self._session.scalar(
            select(Failure).where(
                Failure.repo == repo,
                Failure.run_id == run_id,
                Failure.run_attempt == run_attempt,
            )
        )


class ClassificationRepository:
    """Persistence for classifier verdicts."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to one session."""
        self._session = session

    def add(self, data: ClassificationCreate) -> Classification:
        """Insert a classification and return it."""
        row = Classification(**data.model_dump())
        self._session.add(row)
        self._session.flush()
        return row

    def list_for_failure(self, failure_id: int) -> list[Classification]:
        """Return every classification recorded for a failure, oldest first."""
        return list(
            self._session.scalars(
                select(Classification)
                .where(Classification.failure_id == failure_id)
                .order_by(Classification.id)
            ).all()
        )


class FixCandidateRepository:
    """Persistence for generated patches."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to one session."""
        self._session = session

    def add(self, data: FixCandidateCreate) -> FixCandidate:
        """Insert a fix candidate and return it."""
        row = FixCandidate(**data.model_dump())
        self._session.add(row)
        self._session.flush()
        return row

    def list_for_failure(self, failure_id: int) -> list[FixCandidate]:
        """Return a failure's candidates in the order they were generated."""
        return list(
            self._session.scalars(
                select(FixCandidate)
                .where(FixCandidate.failure_id == failure_id)
                .order_by(FixCandidate.candidate_index)
            ).all()
        )


class DecisionRepository:
    """Persistence for actions, including the keys later workflow runs are matched on."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to one session."""
        self._session = session

    def add(self, data: DecisionCreate) -> Decision:
        """Insert a decision.

        A RETRY must say which run_attempt the re-run will come back as, and a FIX must
        name the `ai-fix` branch CI will run on. Those are the only handles a later
        workflow_run gives us.

        Raises:
            ValueError: A RETRY or FIX is missing the field its later match needs.
        """
        if data.action is DecisionAction.RETRY and data.expected_run_attempt is None:
            raise ValueError("RETRY decisions need expected_run_attempt")
        if data.action is DecisionAction.FIX and not data.fix_branch:
            raise ValueError("FIX decisions need fix_branch")

        row = Decision(**data.model_dump())
        self._session.add(row)
        self._session.flush()
        return row

    def count_retries(self, repo: str, run_id: int) -> int:
        """Count RETRY decisions already stored for this workflow run.

        `retries_done` in the decision engine is this number. A re-run of the same
        `run_id` sees the earlier retry and the engine escalates once the cap is reached.

        Args:
            repo: `owner/name`.
            run_id: GitHub workflow run id, shared by every attempt of that run.

        Returns:
            How many RETRY decisions exist for that pair.
        """
        total = self._session.scalar(
            select(func.count())
            .select_from(Decision)
            .where(
                Decision.action == DecisionAction.RETRY,
                Decision.repo == repo,
                Decision.run_id == run_id,
            )
        )
        return int(total or 0)

    def count_fix_attempts(self, repo: str, base_branch: str) -> int:
        """Count FIX decisions on this branch since its last verified success.

        `attempt` in the decision engine is this number. The branch is the failing
        branch, which is also the pull-request base. A FIX whose outcome is
        `verified_success` resets the count: only FIX rows with a greater id are
        counted. The successful row itself is not an open attempt. Fixes on another
        branch, including an `ai-fix-*` verification branch, are a different lineage.

        There is no time window. The budget starts over when CI on the fix branch
        succeeds, so an old healed failure does not consume `MAX_HEAL_ATTEMPTS`.

        Args:
            repo: `owner/name`.
            base_branch: The branch CI failed on.

        Returns:
            How many FIX decisions on that branch were stored after the latest
            verified success, or all of them when that branch has never verified.
        """
        last_success = (
            select(func.max(Decision.id))
            .select_from(Decision)
            .join(Failure, Decision.failure_id == Failure.id)
            .where(
                Decision.action == DecisionAction.FIX,
                Decision.outcome == DecisionOutcome.VERIFIED_SUCCESS,
                Failure.repo == repo,
                Failure.branch == base_branch,
            )
            .scalar_subquery()
        )
        total = self._session.scalar(
            select(func.count())
            .select_from(Decision)
            .join(Failure, Decision.failure_id == Failure.id)
            .where(
                Decision.action == DecisionAction.FIX,
                Failure.repo == repo,
                Failure.branch == base_branch,
                Decision.id > func.coalesce(last_success, 0),
            )
        )
        return int(total or 0)

    def resolve(self, decision: Decision, outcome: DecisionOutcome) -> Decision:
        """Record how a pending decision ended.

        Args:
            decision: The row to close.
            outcome: The verification or retry result.

        Returns:
            The same decision, after flush.
        """
        decision.outcome = outcome
        decision.resolved_at = utcnow()
        self._session.flush()
        return decision

    def find_open_retry(self, repo: str, run_id: int, run_attempt: int) -> Decision | None:
        """Return the pending RETRY a re-run of this run_attempt should close.

        `run_attempt` is the attempt on the incoming workflow_run, which must equal the
        `expected_run_attempt` stored when the retry was requested.
        """
        return self._session.scalar(
            select(Decision)
            .where(
                Decision.action == DecisionAction.RETRY,
                Decision.outcome == DecisionOutcome.PENDING,
                Decision.repo == repo,
                Decision.run_id == run_id,
                Decision.expected_run_attempt == run_attempt,
            )
            .order_by(Decision.id.desc())
        )

    def record_pull_request(self, decision: Decision, url: str, fix_branch: str) -> Decision:
        """Store the pull request this decision opened, including a dry-run URL.

        Args:
            decision: The FIX row to update.
            url: Real `html_url`, or the dry-run URL the client simulated.
            fix_branch: The `ai-fix-*` branch the pull request uses.

        Returns:
            The same decision, after flush.
        """
        decision.pr_url = url[:512]
        decision.fix_branch = fix_branch[:255]
        self._session.flush()
        return decision

    def record_issue(self, decision: Decision, url: str) -> Decision:
        """Store the issue this decision opened, including a dry-run URL.

        Args:
            decision: The row to update.
            url: Real `html_url`, or the dry-run URL the client simulated.

        Returns:
            The same decision, after flush.
        """
        decision.issue_url = url[:512]
        self._session.flush()
        return decision

    def record_rerun(self, decision: Decision) -> Decision:
        """Mark that a rerun of failed jobs was requested, or would have been.

        Args:
            decision: The RETRY row to update.

        Returns:
            The same decision, after flush.
        """
        decision.rerun_requested = True
        self._session.flush()
        return decision

    def find_open_fix(self, repo: str, fix_branch: str) -> Decision | None:
        """Return the pending FIX whose pull-request branch matches `fix_branch`."""
        return self._session.scalar(
            select(Decision)
            .where(
                Decision.action == DecisionAction.FIX,
                Decision.outcome == DecisionOutcome.PENDING,
                Decision.repo == repo,
                Decision.fix_branch == fix_branch,
            )
            .order_by(Decision.id.desc())
        )

    def list_decisions(
        self,
        *,
        action: DecisionAction | None = None,
        category: FailureCategory | None = None,
        outcome: DecisionOutcome | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Decision], int]:
        """Return a page of decisions, newest first, and the total matching the filters."""
        limit = _bounded_limit(limit)
        offset = _bounded_offset(offset)
        filters = []
        if action is not None:
            filters.append(Decision.action == action)
        if category is not None:
            filters.append(Decision.category == category)
        if outcome is not None:
            filters.append(Decision.outcome == outcome)

        total = self._session.scalar(select(func.count()).select_from(Decision).where(*filters))
        rows = self._session.scalars(
            select(Decision)
            .where(*filters)
            .order_by(Decision.created_at.desc(), Decision.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return list(rows), int(total or 0)
