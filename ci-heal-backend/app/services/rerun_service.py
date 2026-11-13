"""Ask GitHub to rerun the failed jobs of one workflow run."""

import logging

from app.db.models import Decision
from app.db.repositories import DecisionRepository
from app.services.github_client import ActionResult, GitHubClient

logger = logging.getLogger(__name__)


class RerunService:
    """Records `rerun_requested` only after GitHub accepts the request, or in dry-run."""

    def __init__(self, client: GitHubClient) -> None:
        """Bind the service to one GitHub client.

        Args:
            client: The rerun POST is skipped when the client is in dry-run.
        """
        self._client = client

    async def request_rerun(
        self,
        *,
        repo: str,
        run_id: int,
        decision: Decision,
        decisions: DecisionRepository,
    ) -> ActionResult:
        """Rerun failed jobs, or record that a dry-run would have.

        Args:
            repo: `owner/name`.
            run_id: GitHub workflow run id.
            decision: Row that receives `rerun_requested`.
            decisions: Repository that flushes that field.

        Returns:
            A structured result. A 404 does not raise and does not mark the decision.
        """
        result = await self._client.rerun_failed_jobs(repo, run_id)
        if not result.ok:
            logger.info("rerun failed for %s run %s (%s)", repo, run_id, result.error)
            return ActionResult(ok=False, error=result.error, dry_run=result.dry_run)
        decision.dry_run = self._client.dry_run
        decisions.record_rerun(decision)
        return ActionResult(ok=True, dry_run=result.dry_run)
