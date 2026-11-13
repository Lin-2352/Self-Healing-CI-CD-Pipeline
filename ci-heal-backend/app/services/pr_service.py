"""Open one pull request for a fix, or reuse the one already open on that branch.

The branch name is always `ai-fix-<slug>-<sha7>`. A name the model suggested is not read.
"""

import logging
from dataclasses import dataclass

from app.db.models import Decision
from app.db.repositories import DecisionRepository
from app.services.github_client import ActionResult, GitHubClient, GitHubWriteRefused
from app.services.github_text import compose_pull_request, dry_pull_url, make_fix_branch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixRequest:
    """The fix to publish. `head_branch` is the branch CI failed on, not the fix branch."""

    repo: str
    failure_id: int
    head_branch: str
    head_sha: str
    base_branch: str
    file_path: str
    new_content: str
    category: str
    confidence: float
    agreement: float | None
    reasoning: str
    heal_attempt: int
    pr_title: str = ""


class PullRequestService:
    """Creates the fix branch, commits one file, and opens the pull request."""

    def __init__(self, client: GitHubClient) -> None:
        """Bind the service to one GitHub client.

        Args:
            client: Reads always run. Writes no-op when the client is in dry-run.
        """
        self._client = client

    async def open_fix(
        self,
        request: FixRequest,
        decision: Decision,
        decisions: DecisionRepository,
    ) -> ActionResult:
        """Create or reuse the pull request for one FIX decision.

        A failure whose head branch already starts with `ai-fix-` is refused, so a
        fix cannot open another fix for itself.

        Args:
            request: Repo, file contents, and the scores the pull-request body quotes.
            decision: Row that receives `pr_url` and `fix_branch`.
            decisions: Repository that flushes those fields.

        Returns:
            A structured result. 404, 409, and 422 become `error` values and do not raise.
        """
        if request.head_branch.startswith("ai-fix-"):
            logger.info("refusing fix loop on %s branch %s", request.repo, request.head_branch)
            return ActionResult(ok=False, error="fix_loop")

        branch = make_fix_branch(request.category, request.head_sha)
        title, body = compose_pull_request(
            title=request.pr_title,
            category=request.category,
            confidence=request.confidence,
            agreement=request.agreement,
            reasoning=request.reasoning,
            decision_id=decision.id,
            failure_id=request.failure_id,
        )
        try:
            return await self._open(request, decision, decisions, branch, title, body)
        except GitHubWriteRefused as exc:
            logger.info("refusing branch %s", exc.branch)
            return ActionResult(ok=False, error="refused_branch", branch=branch)

    async def _open(
        self,
        request: FixRequest,
        decision: Decision,
        decisions: DecisionRepository,
        branch: str,
        title: str,
        body: str,
    ) -> ActionResult:
        """Look up an existing pull request, then create the branch and commit if needed."""
        existing = await self._client.find_open_pull(request.repo, branch)
        if not existing.ok:
            return ActionResult(ok=False, error=existing.error or "http_error", branch=branch)
        if existing.url:
            self._save(decision, decisions, existing.url, branch)
            return ActionResult(
                ok=True,
                dry_run=self._client.dry_run,
                reused=True,
                url=existing.url,
                branch=branch,
            )

        ref = await self._client.get_branch(request.repo, branch)
        if ref.error == "not_found":
            created = await self._client.create_branch(request.repo, branch, request.head_sha)
            if not created.ok and created.error != "already_exists":
                return ActionResult(
                    ok=False, error=created.error, dry_run=created.dry_run, branch=branch
                )
        elif not ref.ok:
            return ActionResult(ok=False, error=ref.error, branch=branch)

        if self._client.dry_run:
            url = dry_pull_url(request.repo, branch)
            self._save(decision, decisions, url, branch)
            return ActionResult(ok=True, dry_run=True, url=url, branch=branch)

        current = await self._client.get_file(request.repo, request.file_path, branch)
        if not current.ok and current.error != "not_found":
            return ActionResult(ok=False, error=current.error, branch=branch)
        committed = await self._client.commit_file(
            request.repo,
            request.file_path,
            request.new_content,
            f"fix: {request.category} in {request.file_path}",
            branch,
            request.heal_attempt,
            current.sha if current.ok else None,
        )
        if not committed.ok:
            return ActionResult(ok=False, error=committed.error, branch=branch)

        opened = await self._client.create_pull(
            request.repo, title, body, branch, request.base_branch
        )
        if opened.error == "already_exists":
            again = await self._client.find_open_pull(request.repo, branch)
            if again.url:
                self._save(decision, decisions, again.url, branch)
                return ActionResult(ok=True, reused=True, url=again.url, branch=branch)
            return ActionResult(ok=False, error="already_exists", branch=branch)
        if not opened.ok or opened.url is None or opened.number is None:
            return ActionResult(ok=False, error=opened.error or "http_error", branch=branch)

        labeled = await self._client.add_labels(request.repo, opened.number, ["ai-fix"])
        self._save(decision, decisions, opened.url, branch)
        return ActionResult(
            ok=True,
            url=opened.url,
            branch=branch,
            label_applied=labeled.ok,
            label_error=None if labeled.ok else labeled.error,
        )

    def _save(
        self,
        decision: Decision,
        decisions: DecisionRepository,
        url: str,
        branch: str,
    ) -> None:
        """Persist the pull request URL and remember whether this was a dry-run."""
        decision.dry_run = self._client.dry_run
        decisions.record_pull_request(decision, url, branch)
