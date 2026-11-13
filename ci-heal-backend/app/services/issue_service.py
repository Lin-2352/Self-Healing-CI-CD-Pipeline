"""Open one GitHub issue for an escalated failure, or reuse the one already open."""

import logging
from dataclasses import dataclass

from app.db.models import Decision
from app.db.repositories import DecisionRepository
from app.services.github_client import ActionResult, GitHubClient
from app.services.github_text import compose_issue, dry_issue_url, failure_marker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssueRequest:
    """The failure an issue should describe."""

    repo: str
    failure_id: int
    category: str
    confidence: float
    reasoning: str
    title: str = ""


class IssueService:
    """Creates an issue and applies the `ai-fix` label when GitHub allows it."""

    def __init__(self, client: GitHubClient) -> None:
        """Bind the service to one GitHub client.

        Args:
            client: Reads always run. Writes no-op when the client is in dry-run.
        """
        self._client = client

    async def open_issue(
        self,
        request: IssueRequest,
        decision: Decision,
        decisions: DecisionRepository,
    ) -> ActionResult:
        """Create or reuse the issue for one failure.

        The body contains `ci-heal-failure-<id>`. A later call searches for that marker
        and records the existing URL instead of opening a second issue.

        Args:
            request: Repo and the text that will be redacted before it is sent.
            decision: Row that receives `issue_url`.
            decisions: Repository that flushes that field.

        Returns:
            A structured result. Search and create errors do not raise.
        """
        title, body = compose_issue(
            title=request.title,
            category=request.category,
            confidence=request.confidence,
            reasoning=request.reasoning,
            decision_id=decision.id,
            failure_id=request.failure_id,
        )
        existing = await self._client.find_issue(request.repo, failure_marker(request.failure_id))
        if not existing.ok:
            return ActionResult(ok=False, error=existing.error or "http_error")
        if existing.url:
            self._save(decision, decisions, existing.url)
            return ActionResult(
                ok=True, dry_run=self._client.dry_run, reused=True, url=existing.url
            )
        if self._client.dry_run:
            url = dry_issue_url(request.repo, request.failure_id)
            self._save(decision, decisions, url)
            logger.info("dry-run issue recorded for failure %s", request.failure_id)
            return ActionResult(ok=True, dry_run=True, url=url)

        opened = await self._client.create_issue(request.repo, title, body)
        if not opened.ok or opened.url is None or opened.number is None:
            return ActionResult(ok=False, error=opened.error or "http_error")
        labeled = await self._client.add_labels(request.repo, opened.number, ["ai-fix"])
        self._save(decision, decisions, opened.url)
        return ActionResult(
            ok=True,
            url=opened.url,
            label_applied=labeled.ok,
            label_error=None if labeled.ok else labeled.error,
        )

    def _save(self, decision: Decision, decisions: DecisionRepository, url: str) -> None:
        """Persist the issue URL and remember whether this was a dry-run."""
        decision.dry_run = self._client.dry_run
        decisions.record_issue(decision, url)
