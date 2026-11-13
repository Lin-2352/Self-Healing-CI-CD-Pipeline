"""GitHub and n8n intake. Both return quickly and process on a background task.

Payloads are not logged. The worker opens its own database session.
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.enums import DecisionOutcome, FailureSource, ProcessingStatus
from app.db.repositories import DecisionRepository, FailureRepository
from app.deps import get_app_settings, get_db
from app.schemas.failure import FailureCreate
from app.security import github_signature, secrets_equal
from app.services.orchestrator import process_failure

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class N8nPayload(BaseModel):
    """The only fields n8n sends. The workflow run supplies everything else."""

    run_id: int = Field(ge=1)
    repo: str = Field(min_length=1, max_length=255)
    branch: str = Field(min_length=1, max_length=255)
    commit: str = Field(min_length=7, max_length=64)
    actor: str | None = None
    failure_kind: str | None = None
    attempt: int = Field(default=0, ge=0)


class IncomingRun:
    """One completed workflow run, normalised from either webhook."""

    def __init__(
        self,
        *,
        repo: str,
        run_id: int,
        run_attempt: int,
        branch: str,
        commit_sha: str,
        conclusion: str,
        actor: str | None,
        workflow_name: str | None,
        failure_kind: str | None,
        attempt: int,
        source: FailureSource,
    ) -> None:
        """Store the fields both webhooks reduce a delivery to."""
        self.repo = repo
        self.run_id = run_id
        self.run_attempt = run_attempt
        self.branch = branch
        self.commit_sha = commit_sha
        self.conclusion = conclusion
        self.actor = actor
        self.workflow_name = workflow_name
        self.failure_kind = failure_kind
        self.attempt = attempt
        self.source = source


@router.post("/github")
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Accept a GitHub delivery.

    A missing or invalid signature is 401. Outside dev, an unset webhook secret is
    also 401. `ping` returns 200. Only a completed workflow run whose conclusion is
    `failure` or `success` is acted on. Other conclusions return 202 and are ignored.

    Returns:
        200 for ping, 202 when the delivery was accepted or ignored.
    """
    body = await _raw_body(request, settings)
    if not _github_authorized(settings, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="unauthorized")
    if x_github_event == "ping":
        return JSONResponse(status_code=200, content={"status": "pong"})
    payload = _object(body)
    if payload is None or x_github_event != "workflow_run":
        return _accepted("ignored")
    action = payload.get("action")
    run = payload.get("workflow_run")
    if action != "completed" or not isinstance(run, dict):
        return _accepted("ignored")
    conclusion = run.get("conclusion")
    if conclusion not in {"failure", "success"}:
        return _accepted("ignored")
    incoming = _from_github(payload, run, str(conclusion))
    if incoming is None:
        return _accepted("ignored")
    return _intake(incoming, session, settings, background, request)


@router.post("/n8n")
async def n8n_webhook(
    request: Request,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    x_n8n_token: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Accept an n8n failure and load the workflow run from GitHub.

    Returns:
        202 when the delivery was accepted, or 401 when the shared token does not match.
    """
    body = await _raw_body(request, settings)
    expected = settings.n8n_shared_token.get_secret_value()
    if not secrets_equal(x_n8n_token or "", expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    raw = _object(body)
    if raw is None:
        raise HTTPException(status_code=422, detail="invalid body")
    try:
        payload = N8nPayload.model_validate(raw)
    except ValidationError:
        raise HTTPException(status_code=422, detail="invalid body") from None
    if not _repo_allowed(settings, payload.repo):
        raise HTTPException(status_code=403, detail="repo not allowed")
    fetched = await request.app.state.github.get_workflow_run(payload.repo, payload.run_id)
    data = fetched.data if fetched.ok else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="github_unavailable")
    incoming = _from_api(payload, data)
    if incoming is None:
        return _accepted("ignored")
    if incoming.conclusion not in {"failure", "success"}:
        return _accepted("ignored")
    return _intake(incoming, session, settings, background, request)


def _intake(
    incoming: IncomingRun,
    session: Session,
    settings: Settings,
    background: BackgroundTasks,
    request: Request,
) -> JSONResponse:
    """Link verification, or queue a new failure. Duplicate deliveries do not reprocess.

    A completed run on an `ai-fix-*` branch is verification and is recorded even when
    the actor is the token owner. The bot ignore rule applies only to new work.
    """
    if not _repo_allowed(settings, incoming.repo):
        raise HTTPException(status_code=403, detail="repo not allowed")
    if incoming.branch.startswith("ai-fix-"):
        _verify_fix(session, incoming)
        session.commit()
        return _accepted("verified")
    if _is_bot(settings, incoming.actor):
        logger.info("ignored bot run repo=%s run=%s", incoming.repo, incoming.run_id)
        return _accepted("ignored")
    logger.info(
        "workflow_run repo=%s run=%s attempt=%s conclusion=%s",
        incoming.repo,
        incoming.run_id,
        incoming.run_attempt,
        incoming.conclusion,
    )
    decisions = DecisionRepository(session)
    if incoming.conclusion == "success":
        retry = decisions.find_open_retry(incoming.repo, incoming.run_id, incoming.run_attempt)
        if retry is not None:
            decisions.resolve(retry, DecisionOutcome.RETRY_SUCCESS)
            session.commit()
        return _accepted("retry_success" if retry is not None else "ignored")
    retry = decisions.find_open_retry(incoming.repo, incoming.run_id, incoming.run_attempt)
    if retry is not None:
        decisions.resolve(retry, DecisionOutcome.RETRY_FAILURE)
    failure, created = FailureRepository(session).get_or_create(
        FailureCreate(
            repo=incoming.repo,
            run_id=incoming.run_id,
            run_attempt=incoming.run_attempt,
            branch=incoming.branch,
            commit_sha=incoming.commit_sha,
            actor=incoming.actor,
            workflow_name=incoming.workflow_name,
            failure_kind_hint=incoming.failure_kind,
            attempt=incoming.attempt,
            source=incoming.source,
        )
    )
    if not created and failure.processing_status is not ProcessingStatus.ERROR:
        session.commit()
        return _accepted("duplicate")
    if failure.processing_status is ProcessingStatus.ERROR:
        FailureRepository(session).transition_status(failure, ProcessingStatus.QUEUED)
    session.commit()
    state = request.app.state
    background.add_task(
        process_failure,
        failure.id,
        settings=state.settings,
        session_factory=state.session_factory,
        github=state.github,
        classifier_llm=state.classifier_llm,
        fixer_llm=state.fixer_llm,
    )
    return _accepted("queued")


def _verify_fix(session: Session, incoming: IncomingRun) -> None:
    """Close the open FIX for this branch. Never starts a new fix."""
    found = DecisionRepository(session).find_open_fix(incoming.repo, incoming.branch)
    if found is None:
        return
    outcome = (
        DecisionOutcome.VERIFIED_SUCCESS
        if incoming.conclusion == "success"
        else DecisionOutcome.VERIFIED_FAILURE
    )
    DecisionRepository(session).resolve(found, outcome)


async def _raw_body(request: Request, settings: Settings) -> bytes:
    """Read the body, rejecting anything over the configured cap."""
    body = await request.body()
    if len(body) > settings.webhook_max_bytes:
        raise HTTPException(status_code=413, detail="body too large")
    return body


def _github_authorized(settings: Settings, body: bytes, header: str | None) -> bool:
    """True when the signature matches, or when dev has no secret configured."""
    secret = settings.github_webhook_secret.get_secret_value()
    if not secret:
        return settings.app_env == "dev"
    if not header:
        return False
    return secrets_equal(header, github_signature(secret, body))


def _object(body: bytes) -> dict[str, object] | None:
    """Parse a JSON object. Invalid JSON and non-objects return None."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _from_github(
    payload: dict[str, object], run: dict[str, object], conclusion: str
) -> IncomingRun | None:
    """Build an incoming run from a workflow_run payload."""
    repo = _repo_name(payload.get("repository"))
    run_id = _integer(run.get("id"))
    run_attempt = _integer(run.get("run_attempt"))
    branch = run.get("head_branch")
    sha = run.get("head_sha")
    if repo is None or run_id is None or run_attempt is None:
        return None
    if not isinstance(branch, str) or not isinstance(sha, str) or len(sha) < 7:
        return None
    name = run.get("name")
    return IncomingRun(
        repo=repo,
        run_id=run_id,
        run_attempt=run_attempt,
        branch=branch,
        commit_sha=sha,
        conclusion=conclusion,
        actor=_login(run.get("actor")) or _login(run.get("triggering_actor")),
        workflow_name=name if isinstance(name, str) else None,
        failure_kind=None,
        attempt=0,
        source=FailureSource.GITHUB,
    )


def _from_api(payload: N8nPayload, data: dict[str, object]) -> IncomingRun | None:
    """Prefer the workflow-run payload over the n8n body for sha, branch, and attempt."""
    run_attempt = _integer(data.get("run_attempt"))
    if run_attempt is None:
        return None
    branch = data.get("head_branch")
    sha = data.get("head_sha")
    conclusion = data.get("conclusion")
    if not isinstance(branch, str) or not branch:
        branch = payload.branch
    if not isinstance(sha, str) or len(sha) < 7:
        sha = payload.commit
    if not isinstance(conclusion, str):
        conclusion = "failure"
    name = data.get("name")
    actor = _login(data.get("actor")) or _login(data.get("triggering_actor")) or payload.actor
    return IncomingRun(
        repo=payload.repo,
        run_id=payload.run_id,
        run_attempt=run_attempt,
        branch=branch,
        commit_sha=sha,
        conclusion=conclusion,
        actor=actor,
        workflow_name=name if isinstance(name, str) else None,
        failure_kind=payload.failure_kind,
        attempt=payload.attempt,
        source=FailureSource.N8N,
    )


def _repo_name(value: object) -> str | None:
    """Return `full_name` when `value` is a repository object."""
    if not isinstance(value, dict):
        return None
    name = value.get("full_name")
    if isinstance(name, str) and name:
        return name
    return None


def _login(value: object) -> str | None:
    """Return a GitHub login from a user object."""
    if not isinstance(value, dict):
        return None
    login = value.get("login")
    if isinstance(login, str) and login:
        return login
    return None


def _integer(value: object) -> int | None:
    """Return a positive int. Bool is rejected."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _repo_allowed(settings: Settings, repo: str) -> bool:
    """True when `repo` is on the allowlist."""
    return repo in settings.allowed_repo_set


def _is_bot(settings: Settings, actor: str | None) -> bool:
    """True when this actor is the token owner and the run should be ignored."""
    login = settings.github_bot_login.strip().lower()
    if not login or not actor:
        return False
    return actor.strip().lower() == login


def _accepted(status: str) -> JSONResponse:
    """A 202 body. The status is a short label, never a payload."""
    return JSONResponse(status_code=202, content={"status": status})
