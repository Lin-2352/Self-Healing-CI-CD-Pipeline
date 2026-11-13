"""Replay one fixture through the pipeline without touching GitHub or a real database.

The default model is MockLLMClient, scripted from fixtures/labels.json. ``--llm http``
uses the configured classifier and fixer endpoints. Either way the database is
``sqlite:///:memory:`` and DRY_RUN is forced on, so this process cannot record a
verified outcome or leave rows for the stats endpoints.

Run from ci-heal-backend:

    python scripts/replay_failure.py fixtures/dependency.log
    python scripts/replay_failure.py --llm http fixtures/secrets.log
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.core.decision_engine import Stage, decide  # noqa: E402
from app.core.enums import FailureSource  # noqa: E402
from app.db.repositories import (  # noqa: E402
    ClassificationRepository,
    DecisionRepository,
    FailureRepository,
    FixCandidateRepository,
)
from app.db.session import init_db, make_engine, make_session_factory  # noqa: E402
from app.llm.factory import build_llm_client  # noqa: E402
from app.llm.mock import MockLLMClient  # noqa: E402
from app.schemas.failure import FailureCreate  # noqa: E402
from app.services.classifier import build_classifier  # noqa: E402
from app.services.fix_generator import build_fix_generator  # noqa: E402
from app.services.github_client import CallResult, GitHubClient  # noqa: E402
from app.services.orchestrator import Orchestrator  # noqa: E402

_SHA = "abc1234def5678"
_FILE = "def value():\n    return 1\n"
_DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
_MEMORY = "sqlite:///:memory:"


class ReplayGitHub(GitHubClient):
    """GitHub stand-in. Reads return the fixture. Writes stay on the dry-run path."""

    def __init__(self, log: str) -> None:
        """Serve `log` and one tiny file. No socket is opened."""
        super().__init__(token=SecretStr(""), dry_run=True, base_url="https://github.invalid")
        self._log = log

    async def download_logs(self, repo: str, run_id: int) -> CallResult:
        """Return the fixture text."""
        return CallResult(ok=True, text=self._log)

    async def compare(self, repo: str, base: str, head: str) -> CallResult:
        """Return a one-file diff so the pipeline fetches app.py."""
        return CallResult(ok=True, text=_DIFF)

    async def get_file(self, repo: str, path: str, ref: str) -> CallResult:
        """Return the file the scripted patch edits."""
        if path != "app.py":
            return CallResult(ok=False, error="not_found")
        return CallResult(ok=True, text=_FILE, sha="blob")

    async def find_open_pull(self, repo: str, branch: str) -> CallResult:
        """Report that no pull request exists yet."""
        return CallResult(ok=True)

    async def get_branch(self, repo: str, branch: str) -> CallResult:
        """Report that the fix branch does not exist yet."""
        return CallResult(ok=False, error="not_found")

    async def find_issue(self, repo: str, marker: str) -> CallResult:
        """Report that no escalation issue exists yet."""
        return CallResult(ok=True)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one fixture. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Replay one CI fixture through the pipeline.")
    parser.add_argument(
        "fixture",
        nargs="?",
        default=str(ROOT / "fixtures" / "dependency.log"),
        help="Path to a log file. Defaults to fixtures/dependency.log.",
    )
    parser.add_argument(
        "--llm",
        choices=("mock", "http"),
        default="mock",
        help="mock scripts the label. http calls the configured endpoints.",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(Path(args.fixture), args.llm))
    except ValueError as exc:
        print(f"error: {exc}")
        return 2


async def _run(fixture: Path, llm: str) -> int:
    """Load the fixture, run the orchestrator, and print each stage."""
    log_path = fixture if fixture.is_file() else ROOT / fixture
    labels = json.loads((ROOT / "fixtures" / "labels.json").read_text(encoding="utf-8"))
    ground_truth = labels.get(log_path.name)
    if ground_truth is None:
        raise ValueError(f"{log_path.name} is not listed in fixtures/labels.json")
    log = log_path.read_text(encoding="utf-8")
    settings = _settings(llm)
    if not settings.dry_run or settings.database_url != _MEMORY:
        raise ValueError("replay refused to start without dry-run and an in-memory database")

    engine = make_engine(settings.database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        failure, _created = FailureRepository(session).get_or_create(
            FailureCreate(
                repo="acme/demo",
                run_id=1,
                run_attempt=1,
                branch="main",
                commit_sha=_SHA,
                failure_kind_hint="code",
                source=FailureSource.GITHUB,
                ground_truth_category=ground_truth,
            )
        )
        session.commit()
        classifier_llm = _client(settings, "classifier", ground_truth)
        fixer_llm = _client(settings, "fixer", ground_truth)
        await Orchestrator(
            settings=settings,
            github=ReplayGitHub(log),
            classifier=build_classifier(settings, classifier_llm),
            generator=build_fix_generator(settings, fixer_llm),
        ).run(session, failure.id)
        _report(session, failure.id, settings, log_path.name, ground_truth, llm)
        status = FailureRepository(session).get(failure.id)
    finally:
        session.close()
        engine.dispose()
    if status is None or status.processing_status.value != "done":
        return 1
    return 0


def _settings(llm: str) -> Settings:
    """Build settings that cannot write GitHub or a file database.

    ``--llm http`` still forces dry-run and memory. It only changes the model provider.
    """
    file = ".env" if llm == "http" and Path(".env").is_file() else None
    settings = Settings(_env_file=file)
    updates: dict[str, object] = {"dry_run": True, "database_url": _MEMORY}
    if llm == "mock":
        updates["classifier_llm_provider"] = "mock"
        updates["fixer_llm_provider"] = "mock"
    else:
        updates["classifier_llm_provider"] = "http"
        updates["fixer_llm_provider"] = "http"
    return settings.model_copy(update=updates)


def _client(settings: Settings, role: str, ground_truth: str) -> object:
    """Return a mock scripted from the label, or the configured HTTP client."""
    if role == "classifier" and settings.classifier_llm_provider == "mock":
        body = {
            "category": ground_truth,
            "confidence": 0.95,
            "reasoning": "scripted from fixtures/labels.json",
        }
        return MockLLMClient([json.dumps(body)])
    if role == "fixer" and settings.fixer_llm_provider == "mock":
        return MockLLMClient([_proposal()])
    if role == "classifier":
        return build_llm_client(settings, "classifier")
    return build_llm_client(settings, "fixer")


def _proposal() -> str:
    """One SEARCH/REPLACE patch that applies to the replay file."""
    edits = (
        "<<<<<<< SEARCH\n"
        "def value():\n"
        "    return 1\n"
        "=======\n"
        "def value():\n"
        "    return 2\n"
        ">>>>>>> REPLACE\n"
    )
    return json.dumps(
        {
            "branch_name": "ai-fix-ignored",
            "file_path": "app.py",
            "edits": edits,
            "pr_title": "Replay patch",
            "pr_body": "Offline replay. This pull request is dry-run only.",
        }
    )


def _report(
    session: Session,
    failure_id: int,
    settings: Settings,
    name: str,
    ground_truth: str,
    llm: str,
) -> None:
    """Print stages. The log text and any secret it contained are not printed."""
    failure = FailureRepository(session).get(failure_id)
    if failure is None:
        print("failure: missing")
        return
    classifications = ClassificationRepository(session).list_for_failure(failure_id)
    decisions, _total = DecisionRepository(session).list_decisions(limit=20)
    candidates = FixCandidateRepository(session).list_for_failure(failure_id)
    print(f"fixture: {name}")
    print(f"ground_truth: {ground_truth}")
    print("database: sqlite:///:memory: (discarded)")
    print(f"dry_run: {settings.dry_run}")
    print(f"llm: {llm}")
    print(f"redaction_count: {failure.redaction_count}")
    if not classifications:
        print("classification: none")
        return
    latest = classifications[-1]
    print(f"classification: {latest.category.value} confidence={latest.confidence:.3f}")
    for sample in latest.samples:
        item = sample if isinstance(sample, dict) else sample.model_dump()
        category = item.get("category")
        label = category.value if hasattr(category, "value") else category
        print(
            "sample:"
            f" index={item.get('sample_index')}"
            f" category={label}"
            f" confidence={item.get('confidence')}"
            f" parse={item.get('parse_status')}"
        )
    stage1 = decide(
        stage=Stage.CLASSIFICATION,
        category=latest.category,
        confidence=latest.confidence,
        attempt=0,
        retries_done=0,
        thresholds=settings.to_thresholds(),
    )
    print(f"stage1: action={stage1.action.value} reason={stage1.reason_code.value}")
    print(f"candidates: {len(candidates)}")
    for candidate in candidates:
        print(
            f"candidate: index={candidate.candidate_index}"
            f" path={candidate.file_path} applied_ok={candidate.applied_ok}"
        )
    final = decisions[0] if decisions else None
    if final is None:
        print("agreement: not scored")
        print("final: none")
        return
    if final.agreement_score is None:
        print("agreement: not scored")
    else:
        print(f"agreement: {final.agreement_score:.3f}")
    print(
        "final:"
        f" action={final.action.value}"
        f" reason={final.reason}"
        f" outcome={final.outcome.value}"
        f" dry_run={final.dry_run}"
    )
    if final.pr_url:
        print(f"pr_url: {final.pr_url}")
    if final.issue_url:
        print(f"issue_url: {final.issue_url}")
    print(f"processing_status: {failure.processing_status.value}")


if __name__ == "__main__":
    raise SystemExit(main())
