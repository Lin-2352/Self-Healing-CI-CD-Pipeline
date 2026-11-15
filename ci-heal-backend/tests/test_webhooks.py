"""Webhook intake, the pipeline, and API-token protection. GitHub HTTP is respx."""

import base64
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.enums import (
    DecisionAction,
    FailureCategory,
    FailureSource,
    ProcessingStatus,
)
from app.db.repositories import DecisionRepository, FailureRepository
from app.db.session import init_db, make_engine, make_session_factory
from app.db.types import utcnow
from app.llm.mock import MockLLMClient
from app.main import create_app
from app.schemas.decision import DecisionCreate
from app.schemas.failure import FailureCreate
from app.security import github_signature

_SHA = "abc1234def5678"
_SECRET = "hook-secret"
_API = {"Authorization": "Bearer api-secret"}
_FILE = "def value():\n    return 1\n"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "database_url": "sqlite:///:memory:",
        "github_webhook_secret": SecretStr(_SECRET),
        "n8n_shared_token": SecretStr("n8n-secret"),
        "api_token": SecretStr("api-secret"),
        "allowed_repos": "acme/demo",
        "github_token": SecretStr("test-token"),
        "dry_run": True,
        "fix_candidates": 2,
        "classifier_max_retries": 2,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _workflow(
    *,
    run_id: int = 100,
    attempt: int = 1,
    branch: str = "main",
    sha: str = _SHA,
    conclusion: str = "failure",
    actor: str = "octocat",
    repo: str = "acme/demo",
) -> dict[str, object]:
    return {
        "action": "completed",
        "repository": {"full_name": repo},
        "workflow_run": {
            "id": run_id,
            "run_attempt": attempt,
            "head_branch": branch,
            "head_sha": sha,
            "name": "CI",
            "conclusion": conclusion,
            "actor": {"login": actor},
        },
    }


def _post(client: TestClient, payload: dict[str, object], secret: str = _SECRET) -> httpx.Response:
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": github_signature(secret, body),
            "Content-Type": "application/json",
        },
    )


def _zip(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("job.txt", text)
    return buffer.getvalue()


def _arm(*, files: bool = False, pulls: bool = False, search: bool = False) -> None:
    respx.route(host="testserver").pass_through()
    respx.get(url__regex=r".*/logs$").mock(
        return_value=httpx.Response(302, headers={"location": "https://logs.example/run.zip"})
    )
    respx.get("https://logs.example/run.zip").mock(
        return_value=httpx.Response(200, content=_zip("##[error] boom\n"))
    )
    diff = "diff --git a/app.py b/app.py\n" if files else "no file paths\n"
    respx.get(url__regex=r".*/compare/").mock(return_value=httpx.Response(200, text=diff))
    if files:
        encoded = base64.b64encode(_FILE.encode()).decode()
        respx.get(url__regex=r".*/contents/").mock(
            return_value=httpx.Response(
                200,
                json={"type": "file", "size": len(_FILE), "sha": "blob", "content": encoded},
            )
        )
    if pulls:
        respx.get(url__regex=r".*/pulls").mock(return_value=httpx.Response(200, json=[]))
        respx.get(url__regex=r".*/git/ref/heads/").mock(return_value=httpx.Response(404))
    if search:
        respx.get(url__regex=r".*/search/issues").mock(
            return_value=httpx.Response(200, json={"items": []})
        )


def _proposal(replacement: str) -> str:
    edits = f"<<<<<<< SEARCH\ndef value():\n    return 1\n=======\n{replacement}\n>>>>>>> REPLACE\n"
    return json.dumps(
        {
            "branch_name": "ai-fix-from-the-model",
            "file_path": "app.py",
            "edits": edits,
            "pr_title": "Fix the dependency",
            "pr_body": "Pin the package.",
        }
    )


def _use(app: object, classifier: list[str], fixer: list[str] | None = None) -> None:
    app.state.classifier_llm = MockLLMClient(classifier)  # type: ignore[attr-defined]
    if fixer is not None:
        app.state.fixer_llm = MockLLMClient(fixer)  # type: ignore[attr-defined]


def _decisions(client: TestClient) -> list[dict[str, object]]:
    response = client.get("/decisions", headers=_API)
    assert response.status_code == 200
    return response.json()["items"]


def _failures(client: TestClient) -> list[dict[str, object]]:
    response = client.get("/failures", headers=_API)
    assert response.status_code == 200
    return response.json()["items"]


def test_flaky_failure_retries_and_a_later_success_closes_it() -> None:
    settings = _settings()
    app = create_app(settings)
    verdict = json.dumps(
        {"category": "flaky_or_network", "confidence": 0.91, "reasoning": "connection reset"}
    )
    with TestClient(app) as client:
        _use(app, [verdict])
        with respx.mock:
            _arm()
            first = _post(client, _workflow())
            second = _post(client, _workflow(attempt=2, conclusion="success"))
        assert first.status_code == 202
        assert second.status_code == 202
        rows = _decisions(client)
        assert len(rows) == 1
        assert rows[0]["action"] == "retry"
        assert rows[0]["rerun_requested"] is True
        assert rows[0]["outcome"] == "retry_success"
        assert rows[0]["expected_run_attempt"] == 2
        assert len(_failures(client)) == 1


def test_dependency_fix_opens_a_dry_run_pull_request_and_verification_succeeds() -> None:
    settings = _settings()
    app = create_app(settings)
    verdict = json.dumps(
        {"category": "dependency_error", "confidence": 0.95, "reasoning": "unpinned dependency"}
    )
    with TestClient(app) as client:
        _use(app, [verdict], [_proposal("def value():\n    return 2")])
        with respx.mock:
            _arm(files=True, pulls=True)
            opened = _post(client, _workflow())
            verified = _post(
                client,
                _workflow(
                    run_id=200,
                    branch="ai-fix-dependency-error-abc1234",
                    conclusion="success",
                ),
            )
        assert opened.status_code == 202
        assert verified.status_code == 202
        rows = _decisions(client)
        assert len(rows) == 1
        assert rows[0]["action"] == "fix"
        assert rows[0]["dry_run"] is True
        assert str(rows[0]["pr_url"]).startswith("dry-run://")
        assert rows[0]["fix_branch"] == "ai-fix-dependency-error-abc1234"
        assert rows[0]["outcome"] == "verified_success"
        assert len(_failures(client)) == 1


def test_low_agreement_opens_an_issue() -> None:
    settings = _settings()
    app = create_app(settings)
    verdict = json.dumps(
        {"category": "dependency_error", "confidence": 0.95, "reasoning": "unpinned dependency"}
    )
    with TestClient(app) as client:
        _use(
            app,
            [verdict],
            [
                _proposal("def value():\n    return 2"),
                _proposal("VALUE = '" + ("q" * 80) + "'"),
            ],
        )
        with respx.mock:
            _arm(files=True, search=True)
            response = _post(client, _workflow())
        assert response.status_code == 202
        rows = _decisions(client)
        assert rows[0]["action"] == "escalate"
        assert str(rows[0]["issue_url"]).startswith("dry-run://")
        assert rows[0]["reason"] in {"below_agreement_threshold", "agreement_missing"}


def test_malformed_classifier_output_opens_an_issue() -> None:
    settings = _settings()
    app = create_app(settings)
    with TestClient(app) as client:
        _use(app, ["this is not json"])
        with respx.mock:
            _arm(search=True)
            response = _post(client, _workflow())
        assert response.status_code == 202
        rows = _decisions(client)
        assert rows[0]["action"] == "escalate"
        assert rows[0]["category"] == "unknown"
        assert str(rows[0]["issue_url"]).startswith("dry-run://")


def test_bad_hmac_is_rejected() -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        response = _post(client, _workflow(), secret="wrong-secret")
    assert response.status_code == 401


def test_unset_webhook_secret_is_rejected_outside_dev() -> None:
    app = create_app(_settings(app_env="prod"))
    # Startup refuses an empty secret, so clear it after boot to test the request guard.
    app.state.settings = _settings(app_env="prod", github_webhook_secret=SecretStr(""))
    body = json.dumps(_workflow()).encode()
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": "sha256=abc"},
        )
    assert response.status_code == 401


def test_duplicate_delivery_creates_one_failure() -> None:
    settings = _settings()
    app = create_app(settings)
    verdict = json.dumps(
        {"category": "flaky_or_network", "confidence": 0.91, "reasoning": "connection reset"}
    )
    with TestClient(app) as client:
        _use(app, [verdict])
        with respx.mock:
            _arm()
            first = _post(client, _workflow())
            second = _post(client, _workflow())
        assert first.status_code == 202
        assert second.json()["status"] == "duplicate"
        assert len(_failures(client)) == 1
        assert app.state.classifier_llm.calls  # type: ignore[attr-defined]
        assert len(app.state.classifier_llm.calls) == 1  # type: ignore[attr-defined]


def test_a_run_by_the_token_owner_is_ignored() -> None:
    app = create_app(_settings(github_bot_login="ci-heal-bot"))
    with TestClient(app) as client:
        response = _post(client, _workflow(actor="ci-heal-bot"))
        assert response.status_code == 202
        assert response.json()["status"] == "ignored"
        assert _failures(client) == []


def test_repo_outside_the_allowlist_is_rejected() -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        response = _post(client, _workflow(repo="other/demo"))
    assert response.status_code == 403


def test_failure_on_an_ai_fix_branch_only_verifies() -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        session = app.state.session_factory()
        failure, _created = FailureRepository(session).get_or_create(
            FailureCreate(
                repo="acme/demo",
                run_id=50,
                run_attempt=1,
                branch="main",
                commit_sha=_SHA,
                source=FailureSource.GITHUB,
            )
        )
        DecisionRepository(session).add(
            DecisionCreate(
                failure_id=failure.id,
                action=DecisionAction.FIX,
                category=FailureCategory.DEPENDENCY_ERROR,
                reason="fix",
                repo="acme/demo",
                run_id=50,
                fix_branch="ai-fix-dependency-error-abc1234",
                dry_run=True,
            )
        )
        session.commit()
        session.close()
        response = _post(
            client,
            _workflow(
                run_id=300,
                branch="ai-fix-dependency-error-abc1234",
                conclusion="failure",
            ),
        )
        assert response.status_code == 202
        assert response.json()["status"] == "verified"
        rows = _decisions(client)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "verified_failure"
        assert len(_failures(client)) == 1


def test_n8n_wrong_token_is_rejected() -> None:
    app = create_app(_settings())
    body = json.dumps(
        {
            "run_id": 100,
            "repo": "acme/demo",
            "branch": "main",
            "commit": _SHA,
            "actor": "octocat",
            "failure_kind": "code",
            "attempt": 0,
        }
    ).encode()
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/n8n",
            content=body,
            headers={"X-N8N-Token": "nope", "Content-Type": "application/json"},
        )
    assert response.status_code == 401


def test_ai_fix_verification_runs_when_the_actor_is_the_bot() -> None:
    """Verification is recorded before the bot-actor ignore rule."""
    app = create_app(_settings(github_bot_login="octocat"))
    with TestClient(app) as client:
        session = app.state.session_factory()
        failure, _created = FailureRepository(session).get_or_create(
            FailureCreate(
                repo="acme/demo",
                run_id=50,
                run_attempt=1,
                branch="main",
                commit_sha=_SHA,
                source=FailureSource.GITHUB,
            )
        )
        DecisionRepository(session).add(
            DecisionCreate(
                failure_id=failure.id,
                action=DecisionAction.FIX,
                category=FailureCategory.DEPENDENCY_ERROR,
                reason="fix",
                repo="acme/demo",
                run_id=50,
                fix_branch="ai-fix-dependency-error-abc1234",
                dry_run=True,
            )
        )
        session.commit()
        session.close()
        response = _post(
            client,
            _workflow(
                run_id=300,
                branch="ai-fix-dependency-error-abc1234",
                conclusion="success",
                actor="octocat",
            ),
        )
        assert response.status_code == 202
        assert response.json()["status"] == "verified"
        rows = _decisions(client)
        assert rows[0]["outcome"] == "verified_success"
        assert len(_failures(client)) == 1


def test_startup_requeues_a_stuck_processing_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "ci.db"
    url = f"sqlite:///{database.as_posix()}"
    engine = make_engine(url)
    init_db(engine)
    session = make_session_factory(engine)()
    failures = FailureRepository(session)
    waiting, _created = failures.get_or_create(
        FailureCreate(
            repo="acme/demo",
            run_id=3,
            run_attempt=1,
            branch="main",
            commit_sha=_SHA,
            source=FailureSource.GITHUB,
        )
    )
    exhausted, _created = failures.get_or_create(
        FailureCreate(
            repo="acme/demo",
            run_id=4,
            run_attempt=1,
            branch="main",
            commit_sha=_SHA,
            source=FailureSource.GITHUB,
        )
    )
    exhausted.recovery_count = 2
    stuck, _created = failures.get_or_create(
        FailureCreate(
            repo="acme/demo",
            run_id=1,
            run_attempt=1,
            branch="main",
            commit_sha=_SHA,
            source=FailureSource.GITHUB,
        )
    )
    fresh, _created = failures.get_or_create(
        FailureCreate(
            repo="acme/demo",
            run_id=2,
            run_attempt=1,
            branch="main",
            commit_sha=_SHA,
            source=FailureSource.GITHUB,
        )
    )
    failures.transition_status(stuck, ProcessingStatus.PROCESSING)
    failures.transition_status(fresh, ProcessingStatus.PROCESSING)
    stuck.processing_started_at = utcnow() - timedelta(hours=3)
    session.commit()
    session.close()
    engine.dispose()

    async def quiet(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.main.process_failure", quiet)
    app = create_app(
        _settings(
            database_url=url,
            processing_stale_seconds=30,
            max_recoveries=2,
        )
    )
    with TestClient(app):
        assert set(app.state.recovered_ids) == {waiting.id, stuck.id}
        assert len(app.state.recovery_tasks) == 2
    check = app.state.session_factory()
    old = FailureRepository(check).get(stuck.id)
    recent = FailureRepository(check).get(fresh.id)
    parked = FailureRepository(check).get(waiting.id)
    stopped = FailureRepository(check).get(exhausted.id)
    check.close()
    assert old is not None and old.processing_status is ProcessingStatus.QUEUED
    assert old.recovery_count == 1
    assert parked is not None and parked.processing_status is ProcessingStatus.QUEUED
    assert parked.recovery_count == 1
    assert recent is not None and recent.processing_status is ProcessingStatus.PROCESSING
    assert recent.recovery_count == 0
    assert stopped is not None and stopped.processing_status is ProcessingStatus.ERROR


def test_missing_api_token_is_rejected() -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        assert client.get("/failures").status_code == 401
        assert client.get("/decisions").status_code == 401
        assert client.get("/stats").status_code == 401
        assert client.post("/failures/1/reprocess").status_code == 401


def test_reprocess_refuses_a_failure_that_is_processing() -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        session = app.state.session_factory()
        failure, _created = FailureRepository(session).get_or_create(
            FailureCreate(
                repo="acme/demo",
                run_id=9,
                run_attempt=1,
                branch="main",
                commit_sha=_SHA,
                source=FailureSource.GITHUB,
            )
        )
        FailureRepository(session).transition_status(failure, ProcessingStatus.PROCESSING)
        session.commit()
        session.close()
        plain = client.post(f"/failures/{failure.id}/reprocess", headers=_API)
        forced = client.post(f"/failures/{failure.id}/reprocess?force=true", headers=_API)
    assert plain.status_code == 409
    assert forced.status_code == 409


def test_reprocess_force_requeues_a_finished_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.api.failures.process_failure", noop)
    app = create_app(_settings())
    with TestClient(app) as client:
        session = app.state.session_factory()
        failure, _created = FailureRepository(session).get_or_create(
            FailureCreate(
                repo="acme/demo",
                run_id=11,
                run_attempt=1,
                branch="main",
                commit_sha=_SHA,
                source=FailureSource.GITHUB,
            )
        )
        repo = FailureRepository(session)
        repo.transition_status(failure, ProcessingStatus.PROCESSING)
        repo.transition_status(failure, ProcessingStatus.DONE)
        failure.recovery_count = 4
        session.commit()
        session.close()
        refused = client.post(f"/failures/{failure.id}/reprocess", headers=_API)
        accepted = client.post(f"/failures/{failure.id}/reprocess?force=true", headers=_API)
        body = client.get(f"/failures/{failure.id}", headers=_API).json()
    assert refused.status_code == 409
    assert accepted.status_code == 202
    assert body["processing_status"] == "queued"
    assert body["recovery_count"] == 0


def test_orchestrator_exception_marks_error_and_attempts_an_issue() -> None:
    app = create_app(_settings())

    class ExplodingLogs:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        @property
        def dry_run(self) -> bool:
            return self._inner.dry_run  # type: ignore[attr-defined]

        async def download_logs(self, repo: str, run_id: int) -> object:
            raise RuntimeError("boom")

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    with TestClient(app) as client:
        app.state.github = ExplodingLogs(app.state.github)
        with respx.mock:
            respx.route(host="testserver").pass_through()
            respx.get(url__regex=r".*/search/issues").mock(
                return_value=httpx.Response(200, json={"items": []})
            )
            response = _post(client, _workflow())
        assert response.status_code == 202
        failure = _failures(client)[0]
        assert failure["processing_status"] == "error"
        assert failure["processing_error"] == "RuntimeError"
        rows = _decisions(client)
        assert rows[0]["action"] == "escalate"
        assert rows[0]["reason"] == "pipeline_error"
        assert str(rows[0]["issue_url"]).startswith("dry-run://")


def test_only_one_claim_succeeds(db_session: Session) -> None:
    failure, _created = FailureRepository(db_session).get_or_create(
        FailureCreate(
            repo="acme/demo",
            run_id=1,
            run_attempt=1,
            branch="main",
            commit_sha=_SHA,
            source=FailureSource.GITHUB,
        )
    )
    repo = FailureRepository(db_session)
    assert repo.claim(failure.id) is not None
    assert repo.claim(failure.id) is None


def test_ping_is_ok_and_cancelled_is_ignored() -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        ping_body = b'{"zen": "hello"}'
        ping = client.post(
            "/webhooks/github",
            content=ping_body,
            headers={
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": github_signature(_SECRET, ping_body),
            },
        )
        cancelled = _post(client, _workflow(conclusion="cancelled"))
    assert ping.status_code == 200
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "ignored"


@pytest.mark.parametrize("pad", [0, 1, 8, 20, 69, 70, 200])
def test_non_ascii_signature_is_401(pad: int) -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=json.dumps(_workflow()).encode(),
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": ("a" * pad + "é").encode("utf-8"),
            },
        )
    assert response.status_code == 401


@pytest.mark.parametrize("pad", [0, 1, 7, 8, 9, 20])
def test_non_ascii_n8n_token_is_401(pad: int) -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/n8n",
            content=b"{}",
            headers={
                "X-N8N-Token": ("a" * pad + "é").encode("utf-8"),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 401


@pytest.mark.parametrize("pad", [0, 1, 7, 8, 9, 20])
def test_non_ascii_api_token_is_401(pad: int) -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        response = client.get(
            "/failures", headers={"Authorization": b"Bearer " + ("a" * pad + "é").encode("utf-8")}
        )
    assert response.status_code == 401


def _n8n_body() -> bytes:
    return json.dumps(
        {"run_id": 100, "repo": "acme/demo", "branch": "main", "commit": _SHA}
    ).encode()


def _n8n_post(client: TestClient) -> httpx.Response:
    return client.post(
        "/webhooks/n8n",
        content=_n8n_body(),
        headers={"X-N8N-Token": "n8n-secret", "Content-Type": "application/json"},
    )


@respx.mock
@pytest.mark.parametrize(
    "run",
    [
        {"status": "in_progress", "conclusion": None},
        {"status": "completed", "conclusion": None},
        {"status": "completed", "conclusion": ""},
        {"status": "queued"},
    ],
)
def test_n8n_unfinished_run_is_not_healed(run: dict[str, object]) -> None:
    respx.route(host="testserver").pass_through()
    data = {"run_attempt": 1, "head_branch": "main", "head_sha": _SHA, "name": "CI", **run}
    respx.get(url__regex=r".*/actions/runs/100$").mock(return_value=httpx.Response(200, json=data))
    app = create_app(_settings())
    with TestClient(app) as client:
        response = _n8n_post(client)
        assert response.status_code == 409
        assert _failures(client) == []


@respx.mock
def test_n8n_completed_failure_is_still_accepted() -> None:
    respx.route(host="testserver").pass_through()
    data = {
        "run_attempt": 1,
        "head_branch": "main",
        "head_sha": _SHA,
        "name": "CI",
        "status": "completed",
        "conclusion": "failure",
    }
    respx.get(url__regex=r".*/actions/runs/100$").mock(return_value=httpx.Response(200, json=data))
    _arm()
    app = create_app(_settings())
    _use(app, ["{}"])
    with TestClient(app) as client:
        response = _n8n_post(client)
        assert response.status_code == 202
        assert response.json()["status"] == "queued"


def test_fork_pull_request_run_is_ignored() -> None:
    payload = _workflow()
    payload["workflow_run"]["head_repository"] = {"full_name": "mallory/demo"}  # type: ignore[index]
    app = create_app(_settings())
    with TestClient(app) as client:
        response = _post(client, payload)
        assert response.status_code == 202
        assert response.json()["status"] == "ignored"
        assert _failures(client) == []


@respx.mock
def test_same_repo_head_repository_is_still_processed() -> None:
    _arm()
    payload = _workflow()
    payload["workflow_run"]["head_repository"] = {"full_name": "acme/demo"}  # type: ignore[index]
    app = create_app(_settings())
    _use(app, ["{}"])
    with TestClient(app) as client:
        response = _post(client, payload)
        assert response.status_code == 202
        assert response.json()["status"] == "queued"


@respx.mock
def test_n8n_fork_pull_request_run_is_ignored() -> None:
    respx.route(host="testserver").pass_through()
    data = {
        "run_attempt": 1,
        "head_branch": "main",
        "head_sha": _SHA,
        "status": "completed",
        "conclusion": "failure",
        "head_repository": {"full_name": "mallory/demo"},
    }
    respx.get(url__regex=r".*/actions/runs/100$").mock(return_value=httpx.Response(200, json=data))
    app = create_app(_settings())
    with TestClient(app) as client:
        response = _n8n_post(client)
        assert response.status_code == 202
        assert response.json()["status"] == "ignored"
        assert _failures(client) == []
