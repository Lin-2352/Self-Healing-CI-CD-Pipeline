"""Run one failure through classify, decide, and the GitHub action.

The worker opens its own database session. Two workers cannot both leave `queued`:
`FailureRepository.claim` updates only a row that is still queued.

Counters passed to the decision engine:

* `retries_done` is the number of RETRY decisions already stored for the same
  `(repo, run_id)`. A failed re-run is a new failure row and sees that count, so the
  engine escalates once the flaky cap is reached.
* `attempt` is the number of FIX decisions on the same repository and failing branch
  since the last `verified_success` on that branch. A verified fix resets the count.
  There is no time window. Runs on `ai-fix-*` only verify an existing fix.

A pull request uses the failing branch as its base. The `ai-fix-*` branch is created
from the failing commit SHA. The model's branch name is not used.
"""

import asyncio
import logging
import re
from collections.abc import Mapping
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.decision_engine import Action, Decision, Stage, decide
from app.core.enums import DecisionAction, FailureCategory, ProcessingStatus
from app.db.models import Failure
from app.db.repositories import (
    ClassificationRepository,
    DecisionRepository,
    FailureRepository,
    FixCandidateRepository,
)
from app.db.types import utcnow
from app.llm.base import LLMClient
from app.schemas.decision import DecisionCreate
from app.services.classifier import Classifier, build_classifier
from app.services.fix_generator import (
    FixGeneration,
    FixGenerator,
    GeneratedFix,
    build_fix_generator,
)
from app.services.github_client import GitHubClient
from app.services.github_text import make_fix_branch
from app.services.issue_service import IssueRequest, IssueService
from app.services.pr_service import FixRequest, PullRequestService
from app.services.rerun_service import RerunService
from app.services.secret_redactor import prepare_context

logger = logging.getLogger(__name__)

_DIFF_PATH = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_MAX_FILES = 8
_EXCERPT_CHARS = 4000


class Orchestrator:
    """One pass from a claimed failure to a stored decision."""

    def __init__(
        self,
        *,
        settings: Settings,
        github: GitHubClient,
        classifier: Classifier,
        generator: FixGenerator,
    ) -> None:
        """Store the collaborators for one process.

        Args:
            settings: Thresholds, dry-run, and the pipeline timeout.
            github: Reads logs and files. Writes go through its dry-run check.
            classifier: The classifier role.
            generator: The fixer role.
        """
        self._settings = settings
        self._github = github
        self._classifier = classifier
        self._generator = generator
        self._pulls = PullRequestService(github)
        self._issues = IssueService(github)
        self._reruns = RerunService(github)

    async def run(self, session: Session, failure_id: int) -> None:
        """Claim `failure_id` and finish it, or record an error and try to escalate.

        Args:
            session: The worker's session. The caller commits.
            failure_id: Primary key of a queued failure.
        """
        failure = FailureRepository(session).claim(failure_id)
        if failure is None:
            logger.info("failure %s was not queued", failure_id)
            return
        session.commit()
        try:
            await asyncio.wait_for(
                self._pipeline(session, failure),
                timeout=self._settings.pipeline_timeout_seconds,
            )
            session.commit()
        except TimeoutError:
            self._fail(session, failure_id, "pipeline timed out")
            await self._escalate_best_effort(session, failure_id, "pipeline timed out")
            session.commit()
        except Exception as exc:
            logger.warning("pipeline failed for failure %s: %s", failure_id, type(exc).__name__)
            self._fail(session, failure_id, type(exc).__name__)
            await self._escalate_best_effort(session, failure_id, type(exc).__name__)
            session.commit()

    async def _pipeline(self, session: Session, failure: Failure) -> None:
        """Fetch, classify, decide, and act. The caller owns commit and errors."""
        log, diff, files = await self._fetch(failure)
        prepared = prepare_context(log=log, diff=diff, files=files)
        failure.redacted_log_excerpt = str(prepared.log)[:_EXCERPT_CHARS]
        failure.redaction_count = prepared.redaction_count
        session.flush()

        classification = await self._classifier.classify(
            log=prepared.log,
            diff=prepared.diff,
            failure_kind=failure.failure_kind_hint,
        )
        ClassificationRepository(session).add(classification.to_create(failure.id))
        decisions = DecisionRepository(session)
        retries_done = decisions.count_retries(failure.repo, failure.run_id)
        attempt = decisions.count_fix_attempts(failure.repo, failure.branch)
        verdict = decide(
            stage=Stage.CLASSIFICATION,
            category=classification.category,
            confidence=classification.confidence,
            attempt=attempt,
            retries_done=retries_done,
            thresholds=self._settings.to_thresholds(),
        )
        logger.info(
            "failure %s stage=1 action=%s reason=%s",
            failure.id,
            verdict.action.value,
            verdict.reason_code.value,
        )
        if verdict.action is Action.RETRY:
            await self._retry(
                session, failure, verdict, classification.category, classification.confidence
            )
            return
        if verdict.action is Action.ESCALATE:
            await self._issue(
                session,
                failure,
                verdict,
                classification.category,
                classification.confidence,
                classification.reasoning,
                agreement=None,
            )
            return
        generation = await self._generator.generate(
            log=log,
            diff=diff,
            files=files,
            failure_kind=failure.failure_kind_hint,
        )
        for row in generation.to_creates(failure.id):
            FixCandidateRepository(session).add(row)
        verdict = decide(
            stage=Stage.AGREEMENT,
            category=classification.category,
            confidence=classification.confidence,
            attempt=attempt,
            retries_done=retries_done,
            thresholds=self._settings.to_thresholds(),
            agreement_score=generation.agreement.score,
        )
        logger.info(
            "failure %s stage=2 action=%s reason=%s",
            failure.id,
            verdict.action.value,
            verdict.reason_code.value,
        )
        chosen = _chosen(generation)
        if verdict.action is Action.FIX and chosen is not None and chosen.new_content:
            await self._fix(
                session,
                failure,
                verdict,
                classification.category,
                classification.confidence,
                classification.reasoning,
                generation.agreement.score,
                chosen,
            )
            return
        await self._issue(
            session,
            failure,
            verdict,
            classification.category,
            classification.confidence,
            classification.reasoning,
            agreement=generation.agreement.score,
        )

    async def _fetch(self, failure: Failure) -> tuple[str, str, dict[str, str]]:
        """Load the log, the commit diff, and the files that diff touches.

        Raises:
            RuntimeError: The log could not be downloaded. The message is a stable code.
        """
        logs = await self._github.download_logs(failure.repo, failure.run_id)
        if not logs.ok or logs.text is None:
            raise RuntimeError("log_unavailable")
        parent = f"{failure.commit_sha}~1"
        compared = await self._github.compare(failure.repo, parent, failure.commit_sha)
        if not compared.ok or compared.text is None:
            compared = await self._github.compare(failure.repo, failure.branch, failure.commit_sha)
        diff = compared.text or ""
        files: dict[str, str] = {}
        for path in _changed_paths(diff):
            remote = await self._github.get_file(failure.repo, path, failure.commit_sha)
            if remote.ok and remote.text is not None:
                files[path] = remote.text
        return logs.text, diff, files

    async def _retry(
        self,
        session: Session,
        failure: Failure,
        verdict: Decision,
        category: FailureCategory,
        confidence: float,
    ) -> None:
        """Store a RETRY and ask GitHub to rerun the failed jobs."""
        decisions = DecisionRepository(session)
        row = decisions.add(
            _decision_create(
                failure,
                verdict,
                action=DecisionAction.RETRY,
                category=category,
                confidence=confidence,
                agreement=None,
                dry_run=self._settings.dry_run,
                expected_run_attempt=failure.run_attempt + 1,
            )
        )
        result = await self._reruns.request_rerun(
            repo=failure.repo,
            run_id=failure.run_id,
            decision=row,
            decisions=decisions,
        )
        self._finish(session, failure, result.ok, result.error)

    async def _fix(
        self,
        session: Session,
        failure: Failure,
        verdict: Decision,
        category: FailureCategory,
        confidence: float,
        reasoning: str,
        agreement: float | None,
        chosen: GeneratedFix,
    ) -> None:
        """Store a FIX and open a pull request from the failing commit."""
        decisions = DecisionRepository(session)
        branch = make_fix_branch(category.value, failure.commit_sha)
        row = decisions.add(
            _decision_create(
                failure,
                verdict,
                action=DecisionAction.FIX,
                category=category,
                confidence=confidence,
                agreement=agreement,
                dry_run=self._settings.dry_run,
                fix_branch=branch,
            )
        )
        result = await self._pulls.open_fix(
            FixRequest(
                repo=failure.repo,
                failure_id=failure.id,
                head_branch=failure.branch,
                head_sha=failure.commit_sha,
                base_branch=failure.branch,
                file_path=chosen.file_path,
                new_content=chosen.new_content or "",
                category=category.value,
                confidence=confidence,
                agreement=agreement,
                reasoning=reasoning,
                heal_attempt=decisions.count_fix_attempts(failure.repo, failure.branch),
                pr_title=chosen.pr_title or "",
            ),
            row,
            decisions,
        )
        self._finish(session, failure, result.ok, result.error)

    async def _issue(
        self,
        session: Session,
        failure: Failure,
        verdict: Decision,
        category: FailureCategory,
        confidence: float,
        reasoning: str,
        agreement: float | None,
    ) -> None:
        """Store an ESCALATE decision and open an issue."""
        decisions = DecisionRepository(session)
        row = decisions.add(
            _decision_create(
                failure,
                verdict,
                action=DecisionAction.ESCALATE,
                category=category,
                confidence=confidence,
                agreement=agreement,
                dry_run=self._settings.dry_run,
            )
        )
        result = await self._issues.open_issue(
            IssueRequest(
                repo=failure.repo,
                failure_id=failure.id,
                category=category.value,
                confidence=confidence,
                reasoning=reasoning,
            ),
            row,
            decisions,
        )
        self._finish(session, failure, result.ok, result.error)

    def _finish(self, session: Session, failure: Failure, ok: bool, error: str | None) -> None:
        """Mark the failure done, or error when the GitHub action did not succeed."""
        failures = FailureRepository(session)
        if ok:
            failures.transition_status(failure, ProcessingStatus.DONE)
            return
        failures.transition_status(failure, ProcessingStatus.ERROR, error or "action_failed")

    def _fail(self, session: Session, failure_id: int, message: str) -> None:
        """Roll back the unfinished pass and record `message` on the failure."""
        session.rollback()
        failure = FailureRepository(session).get(failure_id)
        if failure is None or failure.processing_status is not ProcessingStatus.PROCESSING:
            return
        FailureRepository(session).transition_status(failure, ProcessingStatus.ERROR, message)

    async def _escalate_best_effort(self, session: Session, failure_id: int, message: str) -> None:
        """Open an escalation issue after an unexpected failure. Errors here are swallowed."""
        failure = FailureRepository(session).get(failure_id)
        if failure is None:
            return
        try:
            decisions = DecisionRepository(session)
            row = decisions.add(
                DecisionCreate(
                    failure_id=failure.id,
                    action=DecisionAction.ESCALATE,
                    category=FailureCategory.UNKNOWN,
                    classifier_confidence=0,
                    reason="pipeline_error",
                    thresholds_snapshot=_plain(self._settings.to_thresholds().by_category),
                    dry_run=self._settings.dry_run,
                    repo=failure.repo,
                    run_id=failure.run_id,
                )
            )
            await self._issues.open_issue(
                IssueRequest(
                    repo=failure.repo,
                    failure_id=failure.id,
                    category=FailureCategory.UNKNOWN.value,
                    confidence=0,
                    reasoning=message,
                ),
                row,
                decisions,
            )
        except Exception as exc:
            logger.warning(
                "escalation issue failed for failure %s: %s", failure_id, type(exc).__name__
            )


def recover_stuck(
    factory: sessionmaker[Session], stale_seconds: int, max_recoveries: int
) -> list[int]:
    """Requeue stale work and return the ids startup should schedule.

    Queued rows are included, and so are processing rows older than `stale_seconds`.
    Each one increments `recovery_count`. A row already at `max_recoveries` is marked
    error and left off the list, so a crash loop stops.

    Args:
        factory: Session factory for this process.
        stale_seconds: Age past which a processing row is no longer considered live.
        max_recoveries: Startup reschedules allowed before a row is marked error.

    Returns:
        Failure ids that are queued and should be processed. Empty when recovery fails.
    """
    session = factory()
    try:
        scheduled = FailureRepository(session).recover(
            utcnow() - timedelta(seconds=stale_seconds),
            max_recoveries,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("stuck-processing recovery failed: %s", type(exc).__name__)
        return []
    finally:
        session.close()
    if scheduled:
        logger.info("scheduled %s recovered failure(s)", len(scheduled))
    return scheduled


async def process_failure(
    failure_id: int,
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    github: GitHubClient,
    classifier_llm: LLMClient,
    fixer_llm: LLMClient,
) -> None:
    """Run the pipeline for one failure on a fresh session.

    Background tasks call this. It does not reuse the request session.

    Args:
        failure_id: The queued failure.
        settings: Process settings.
        session_factory: Builds the worker session.
        github: GitHub client from `app.state`.
        classifier_llm: Classifier model client.
        fixer_llm: Fixer model client.
    """
    session = session_factory()
    try:
        await Orchestrator(
            settings=settings,
            github=github,
            classifier=build_classifier(settings, classifier_llm),
            generator=build_fix_generator(settings, fixer_llm),
        ).run(session, failure_id)
    except Exception as exc:
        session.rollback()
        logger.warning("worker failed for failure %s: %s", failure_id, type(exc).__name__)
    finally:
        session.close()


def _decision_create(
    failure: Failure,
    verdict: Decision,
    *,
    action: DecisionAction,
    category: FailureCategory,
    confidence: float | None,
    agreement: float | None,
    dry_run: bool,
    expected_run_attempt: int | None = None,
    fix_branch: str | None = None,
) -> DecisionCreate:
    """Build the stored decision, including the threshold snapshot and reason code."""
    return DecisionCreate(
        failure_id=failure.id,
        action=action,
        category=category,
        classifier_confidence=confidence,
        agreement_score=agreement,
        reason=verdict.reason_code.value,
        thresholds_snapshot=_plain_snapshot(verdict.thresholds_snapshot),
        dry_run=dry_run,
        repo=failure.repo,
        run_id=failure.run_id,
        expected_run_attempt=expected_run_attempt,
        fix_branch=fix_branch,
    )


def _plain_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    """Copy a threshold snapshot into plain dicts the JSON column can store."""
    data = dict(snapshot)
    categories = data.get("category_thresholds")
    if isinstance(categories, Mapping):
        data["category_thresholds"] = dict(categories)
    return data


def _plain(categories: Mapping[str, float]) -> dict[str, object]:
    """A small snapshot used when the engine never ran."""
    return {"category_thresholds": dict(categories)}


def _chosen(generation: FixGeneration) -> GeneratedFix | None:
    """Return the applicable medoid, when the scorer picked one."""
    index = generation.agreement.chosen_index
    if index is None:
        return None
    for candidate in generation.candidates:
        if candidate.index == index and candidate.applied_ok and candidate.new_content:
            return candidate
    return None


def _changed_paths(diff: str) -> list[str]:
    """Paths a unified diff edits, skipping deletions and keeping at most eight."""
    paths: list[str] = []
    for match in _DIFF_PATH.finditer(diff):
        path = match.group(2)
        if path in {"/dev/null", ""} or path.startswith("/") or path in paths:
            continue
        paths.append(path)
        if len(paths) >= _MAX_FILES:
            break
    return paths
