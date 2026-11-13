"""GitHub client and the pull-request, issue, and rerun services. HTTP is respx."""

import asyncio
import base64
import io
import json
import zipfile

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.enums import DecisionAction, FailureCategory, FailureSource
from app.db.repositories import DecisionRepository, FailureRepository
from app.schemas.decision import DecisionCreate
from app.schemas.failure import FailureCreate
from app.services.github_client import GitHubClient, GitHubWriteRefused
from app.services.github_text import AI_NOTE
from app.services.issue_service import IssueRequest, IssueService
from app.services.pr_service import FixRequest, PullRequestService
from app.services.rerun_service import RerunService

_BASE = "https://git.example.test/api"
_REPO = "acme/demo"
_SHA = "abc1234def5678"
_BRANCH = "ai-fix-dependency-error-abc1234"
_TOKEN = "super-secret-token"
_SECRET = "ghp_" + ("a" * 36)


def _client(**overrides: object) -> GitHubClient:
    settings: dict[str, object] = {
        "token": SecretStr(_TOKEN),
        "base_url": _BASE,
        "dry_run": False,
        "timeout": 5,
        "max_retries": 2,
        "default_branch": "main",
    }
    settings.update(overrides)
    return GitHubClient(**settings)  # type: ignore[arg-type]


def _failure(**overrides: object) -> FailureCreate:
    data: dict[str, object] = {
        "repo": _REPO,
        "run_id": 100,
        "run_attempt": 1,
        "branch": "main",
        "commit_sha": _SHA,
        "source": FailureSource.GITHUB,
    }
    data.update(overrides)
    return FailureCreate(**data)  # type: ignore[arg-type]


def _fix_decision(failure_id: int) -> DecisionCreate:
    return DecisionCreate(
        failure_id=failure_id,
        action=DecisionAction.FIX,
        category=FailureCategory.DEPENDENCY_ERROR,
        reason="agreement passed",
        repo=_REPO,
        run_id=100,
        fix_branch="ai-fix-pending",
    )


def _request(failure_id: int, **overrides: object) -> FixRequest:
    data: dict[str, object] = {
        "repo": _REPO,
        "failure_id": failure_id,
        "head_branch": "main",
        "head_sha": _SHA,
        "base_branch": "main",
        "file_path": "app.py",
        "new_content": "x = 2\n",
        "category": "dependency_error",
        "confidence": 0.91,
        "agreement": 0.8,
        "reasoning": "pin the dependency",
        "heal_attempt": 1,
        "pr_title": "Fix the lockfile",
    }
    data.update(overrides)
    return FixRequest(**data)  # type: ignore[arg-type]


def _write_count() -> int:
    return sum(call.request.method in {"POST", "PUT", "PATCH", "DELETE"} for call in respx.calls)


def test_each_write_endpoint_uses_the_configured_base_url() -> None:
    """Live writes hit the API root from settings and carry the heal-attempt trailer."""

    async def scenario() -> None:
        client = _client()
        assert _TOKEN not in repr(client)
        async with respx.mock:
            refs = respx.post(f"{_BASE}/repos/{_REPO}/git/refs").mock(
                return_value=httpx.Response(201, json={"ref": f"refs/heads/{_BRANCH}"})
            )
            contents = respx.put(f"{_BASE}/repos/{_REPO}/contents/app.py").mock(
                return_value=httpx.Response(200, json={"content": {"sha": "blob"}})
            )
            pulls = respx.post(f"{_BASE}/repos/{_REPO}/pulls").mock(
                return_value=httpx.Response(
                    201, json={"html_url": "https://github.com/acme/demo/pull/4", "number": 4}
                )
            )
            issues = respx.post(f"{_BASE}/repos/{_REPO}/issues").mock(
                return_value=httpx.Response(
                    201, json={"html_url": "https://github.com/acme/demo/issues/8", "number": 8}
                )
            )
            labels = respx.post(f"{_BASE}/repos/{_REPO}/issues/4/labels").mock(
                return_value=httpx.Response(200, json=[{"name": "ai-fix"}])
            )
            rerun = respx.post(f"{_BASE}/repos/{_REPO}/actions/runs/100/rerun-failed-jobs").mock(
                return_value=httpx.Response(201)
            )

            branch = await client.create_branch(_REPO, _BRANCH, _SHA)
            committed = await client.commit_file(
                _REPO, "app.py", "x = 1\n", "fix: demo", _BRANCH, 2, "blobsha"
            )
            pull = await client.create_pull(_REPO, "title", "body", _BRANCH, "main")
            issue = await client.create_issue(_REPO, "title", "body")
            labeled = await client.add_labels(_REPO, 4, ["ai-fix"])
            reran = await client.rerun_failed_jobs(_REPO, 100)

        assert branch.ok and committed.ok and pull.ok and issue.ok and labeled.ok and reran.ok
        assert pull.number == 4
        assert issue.number == 8
        ref_body = json.loads(refs.calls[0].request.content)
        assert ref_body == {"ref": f"refs/heads/{_BRANCH}", "sha": _SHA}
        assert refs.calls[0].request.headers["authorization"] == f"Bearer {_TOKEN}"
        commit_body = json.loads(contents.calls[0].request.content)
        assert commit_body["branch"] == _BRANCH
        assert commit_body["sha"] == "blobsha"
        assert "Heal-Attempt: 2\n" in commit_body["message"]
        assert base64.b64decode(commit_body["content"]).decode() == "x = 1\n"
        assert json.loads(pulls.calls[0].request.content)["head"] == _BRANCH
        assert issues.called
        assert labels.called
        assert rerun.called

    asyncio.run(scenario())


def test_dry_run_sends_no_write_requests() -> None:
    """With no write routes registered, a dry-run of every write still succeeds."""

    async def scenario() -> None:
        client = _client(dry_run=True)
        async with respx.mock:
            assert (await client.create_branch(_REPO, _BRANCH, _SHA)).dry_run is True
            assert (await client.commit_file(_REPO, "app.py", "x", "m", _BRANCH, 1)).ok is True
            assert (await client.create_pull(_REPO, "t", "b", _BRANCH, "main")).dry_run is True
            assert (await client.create_issue(_REPO, "t", "b")).dry_run is True
            assert (await client.add_labels(_REPO, 1, ["ai-fix"])).dry_run is True
            assert (await client.rerun_failed_jobs(_REPO, 100)).dry_run is True
            assert respx.calls.call_count == 0

    asyncio.run(scenario())


def test_non_ai_fix_branch_write_is_refused() -> None:
    """A branch we did not generate never reaches the network, even in dry-run."""

    async def scenario() -> None:
        client = _client(dry_run=True)
        async with respx.mock:
            for branch in ("feature", "fix/thing", "ai-fix-bad/name"):
                with pytest.raises(GitHubWriteRefused):
                    await client.create_branch(_REPO, branch, _SHA)
                with pytest.raises(GitHubWriteRefused):
                    await client.commit_file(_REPO, "app.py", "x", "m", branch, 1)
                with pytest.raises(GitHubWriteRefused):
                    await client.create_pull(_REPO, "t", "b", branch, "main")
            assert respx.calls.call_count == 0

    asyncio.run(scenario())


def test_default_branch_write_is_refused() -> None:
    """main, master, and the configured default branch are refused even in dry-run."""

    async def scenario() -> None:
        client = _client(default_branch="develop", dry_run=True)
        guarded = _client(default_branch="ai-fix-main", dry_run=True)
        async with respx.mock:
            for branch in ("main", "master", "develop"):
                with pytest.raises(GitHubWriteRefused):
                    await client.create_branch(_REPO, branch, _SHA)
                with pytest.raises(GitHubWriteRefused):
                    await client.commit_file(_REPO, "a.py", "x", "m", branch, 1)
            with pytest.raises(GitHubWriteRefused):
                await client.create_pull(_REPO, "t", "b", "main", "develop")
            with pytest.raises(GitHubWriteRefused):
                await guarded.create_branch(_REPO, "ai-fix-main", _SHA)
            assert respx.calls.call_count == 0

    asyncio.run(scenario())


def test_retry_after_and_rate_limit_reset_are_honoured() -> None:
    """429 waits for Retry-After. A secondary 403 waits until X-RateLimit-Reset."""

    async def scenario() -> None:
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            delays.append(seconds)

        client = _client(sleep=sleep, clock=lambda: 1000.0, max_retries=2)
        url = f"{_BASE}/repos/{_REPO}/actions/runs/1"
        async with respx.mock:
            route = respx.get(url).mock(
                side_effect=[
                    httpx.Response(429, headers={"retry-after": "2"}),
                    httpx.Response(200, json={"id": 1, "head_sha": _SHA}),
                ]
            )
            result = await client.get_workflow_run(_REPO, 1)
        assert result.ok is True
        assert result.sha == _SHA
        assert delays == [2.0]
        assert route.call_count == 2

        delays.clear()
        async with respx.mock:
            respx.get(url).mock(
                side_effect=[
                    httpx.Response(
                        403,
                        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1005"},
                    ),
                    httpx.Response(200, json={"id": 1}),
                ]
            )
            assert (await client.get_workflow_run(_REPO, 1)).ok is True
        assert delays == [5.0]

        delays.clear()
        async with respx.mock:
            blocked = respx.get(url).mock(return_value=httpx.Response(403))
            assert (await client.get_workflow_run(_REPO, 1)).error == "forbidden"
        assert blocked.call_count == 1
        assert delays == []

    asyncio.run(scenario())


def test_jobs_are_paginated_logs_unzip_and_large_files_are_not_decoded() -> None:
    """Reads cover the next-page link, the log redirect, and the file size cap."""

    async def scenario() -> None:
        client = _client(max_file_bytes=10, max_log_bytes=10_000)
        jobs = f"{_BASE}/repos/{_REPO}/actions/runs/9/jobs"
        async with respx.mock:
            respx.get(url__regex=r".*/actions/runs/9/jobs").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={"jobs": [{"id": 1}]},
                        headers={"link": f'<{jobs}?page=2>; rel="next"'},
                    ),
                    httpx.Response(200, json={"jobs": [{"id": 2}]}),
                ]
            )
            listed = await client.list_jobs(_REPO, 9)
        assert [item["id"] for item in listed.items] == [1, 2]  # type: ignore[index]

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("build/1.txt", "boom-line\n")
        async with respx.mock:
            respx.get(f"{_BASE}/repos/{_REPO}/actions/runs/5/logs").mock(
                return_value=httpx.Response(
                    302, headers={"location": "https://logs.example/run.zip"}
                )
            )
            zip_route = respx.get("https://logs.example/run.zip").mock(
                return_value=httpx.Response(200, content=buffer.getvalue())
            )
            logs = await client.download_logs(_REPO, 5)
        assert logs.text is not None and "boom-line" in logs.text
        assert "authorization" not in zip_route.calls[0].request.headers

        encoded = base64.b64encode(b"SHOULD_NOT_DECODE").decode()
        async with respx.mock:
            respx.get(url__regex=r".*/contents/app.py").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "type": "file",
                        "size": 50,
                        "sha": "blob",
                        "content": encoded,
                    },
                )
            )
            oversized = await client.get_file(_REPO, "app.py", _SHA)
        assert oversized.error == "too_large"
        assert oversized.text is None

        async with respx.mock:
            diff = respx.get(url__regex=r".*/compare/main\.\.\.abc1234").mock(
                return_value=httpx.Response(200, text="diff --git a/app.py\n")
            )
            compared = await client.compare(_REPO, "main", "abc1234")
        assert compared.text == "diff --git a/app.py\n"
        assert diff.calls[0].request.headers["accept"] == "application/vnd.github.diff"

    asyncio.run(scenario())


def test_fix_loop_on_an_ai_fix_branch_does_not_write(db_session: Session) -> None:
    """A failure that already ran on an ai-fix branch is a structured error."""
    failure, _created = FailureRepository(db_session).get_or_create(
        _failure(branch="ai-fix-old-abc1234")
    )
    decision = DecisionRepository(db_session).add(_fix_decision(failure.id))
    service = PullRequestService(_client())

    async def scenario() -> None:
        async with respx.mock:
            result = await service.open_fix(
                _request(failure.id, head_branch="ai-fix-old-abc1234"),
                decision,
                DecisionRepository(db_session),
            )
            assert respx.calls.call_count == 0
        assert result.error == "fix_loop"
        assert decision.pr_url is None

    asyncio.run(scenario())


def test_idempotent_rerun_reuses_the_open_pull_request(db_session: Session) -> None:
    """A second pass finds the open pull request and does not post another one."""
    failure, _created = FailureRepository(db_session).get_or_create(_failure())
    decisions = DecisionRepository(db_session)
    decision = decisions.add(_fix_decision(failure.id))
    service = PullRequestService(_client())
    phase = {"open": False}

    def pulls(_request: httpx.Request) -> httpx.Response:
        if phase["open"]:
            return httpx.Response(
                200,
                json=[{"html_url": "https://github.com/acme/demo/pull/4", "number": 4}],
            )
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        async with respx.mock:
            respx.get(url__regex=r".*/pulls").mock(side_effect=pulls)
            respx.get(url__regex=r".*/git/ref/heads/").mock(return_value=httpx.Response(404))
            respx.post(url__regex=r".*/git/refs$").mock(return_value=httpx.Response(201, json={}))
            respx.get(url__regex=r".*/contents/").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "type": "file",
                        "size": 4,
                        "sha": "blobsha",
                        "content": base64.b64encode(b"x=1\n").decode(),
                    },
                )
            )
            puts = respx.put(url__regex=r".*/contents/").mock(
                return_value=httpx.Response(200, json={})
            )
            created = respx.post(url__regex=r".*/pulls$").mock(
                return_value=httpx.Response(
                    201,
                    json={"html_url": "https://github.com/acme/demo/pull/4", "number": 4},
                )
            )
            respx.post(url__regex=r".*/labels$").mock(return_value=httpx.Response(200, json=[]))

            first = await service.open_fix(_request(failure.id), decision, decisions)
            writes = _write_count()
            phase["open"] = True
            second = await service.open_fix(_request(failure.id), decision, decisions)
            assert _write_count() == writes
            assert all(str(call.request.url).startswith(_BASE) for call in respx.calls)

        assert first.ok is True
        assert first.branch == _BRANCH
        assert first.url == "https://github.com/acme/demo/pull/4"
        assert decision.pr_url == first.url
        assert decision.fix_branch == _BRANCH
        assert "Heal-Attempt: 1\n" in json.loads(puts.calls[0].request.content)["message"]
        assert json.loads(puts.calls[0].request.content)["sha"] == "blobsha"
        assert created.call_count == 1
        assert second.reused is True
        assert second.url == first.url

    asyncio.run(scenario())


def test_branch_already_exists_still_opens_the_pull_request(db_session: Session) -> None:
    """A 422 from creating the ref is `already_exists` and the commit still proceeds."""
    failure, _created = FailureRepository(db_session).get_or_create(_failure(run_id=101))
    decisions = DecisionRepository(db_session)
    decision = decisions.add(_fix_decision(failure.id))
    service = PullRequestService(_client())

    async def scenario() -> None:
        async with respx.mock:
            respx.get(url__regex=r".*/pulls").mock(return_value=httpx.Response(200, json=[]))
            respx.get(url__regex=r".*/git/ref/heads/").mock(return_value=httpx.Response(404))
            refs = respx.post(url__regex=r".*/git/refs$").mock(
                return_value=httpx.Response(422, json={"message": "Reference already exists"})
            )
            respx.get(url__regex=r".*/contents/").mock(return_value=httpx.Response(404))
            puts = respx.put(url__regex=r".*/contents/").mock(
                return_value=httpx.Response(201, json={})
            )
            respx.post(url__regex=r".*/pulls$").mock(
                return_value=httpx.Response(
                    201,
                    json={"html_url": "https://github.com/acme/demo/pull/9", "number": 9},
                )
            )
            respx.post(url__regex=r".*/labels$").mock(return_value=httpx.Response(200, json=[]))
            result = await service.open_fix(_request(failure.id), decision, decisions)

        assert refs.call_count == 1
        assert puts.called
        assert result.ok is True
        assert result.url == "https://github.com/acme/demo/pull/9"
        assert decision.pr_url == result.url

    asyncio.run(scenario())


def test_outbound_text_is_redacted_and_capped(db_session: Session) -> None:
    """A token in the title or reasoning never reaches GitHub, and both fields are capped."""
    failure, _created = FailureRepository(db_session).get_or_create(_failure(run_id=102))
    decisions = DecisionRepository(db_session)
    decision = decisions.add(_fix_decision(failure.id))
    service = PullRequestService(_client())
    request = _request(
        failure.id,
        pr_title=("T" * 80) + _SECRET + ("T" * 80),
        reasoning=f"leaked {_SECRET}\n" + (("x" * 40) + "\n") * 1600,
    )

    async def scenario() -> None:
        async with respx.mock:
            respx.get(url__regex=r".*/pulls").mock(return_value=httpx.Response(200, json=[]))
            respx.get(url__regex=r".*/git/ref/heads/").mock(return_value=httpx.Response(404))
            respx.post(url__regex=r".*/git/refs$").mock(return_value=httpx.Response(201, json={}))
            respx.get(url__regex=r".*/contents/").mock(return_value=httpx.Response(404))
            respx.put(url__regex=r".*/contents/").mock(return_value=httpx.Response(201, json={}))
            pulls = respx.post(url__regex=r".*/pulls$").mock(
                return_value=httpx.Response(
                    201,
                    json={"html_url": "https://github.com/acme/demo/pull/11", "number": 11},
                )
            )
            respx.post(url__regex=r".*/labels$").mock(return_value=httpx.Response(200, json=[]))
            result = await service.open_fix(request, decision, decisions)

        body = json.loads(pulls.calls[0].request.content)
        assert result.ok is True
        assert _SECRET not in body["title"]
        assert _SECRET not in body["body"]
        assert "ghp_" not in body["title"]
        assert "ghp_" not in body["body"]
        assert "[REDACTED:GITHUB_TOKEN]" in body["title"]
        assert "[REDACTED:GITHUB_TOKEN]" in body["body"]
        assert len(body["title"]) <= 120
        assert len(body["body"]) < 60_000
        assert AI_NOTE in body["body"]
        assert "Category: dependency_error" in body["body"]
        assert "Classifier confidence: 0.91" in body["body"]
        assert "Agreement: 0.80" in body["body"]
        assert f"Decision: {decision.id}" in body["body"]
        assert f"ci-heal-failure-{failure.id}" in body["body"]
        assert body["head"] == _BRANCH

    asyncio.run(scenario())


def test_label_failure_does_not_fail_the_pull_request(db_session: Session) -> None:
    """A 404 from adding `ai-fix` leaves the pull request recorded and ok."""
    failure, _created = FailureRepository(db_session).get_or_create(_failure(run_id=103))
    decisions = DecisionRepository(db_session)
    decision = decisions.add(_fix_decision(failure.id))
    service = PullRequestService(_client())

    async def scenario() -> None:
        async with respx.mock:
            respx.get(url__regex=r".*/pulls").mock(return_value=httpx.Response(200, json=[]))
            respx.get(url__regex=r".*/git/ref/heads/").mock(return_value=httpx.Response(404))
            respx.post(url__regex=r".*/git/refs$").mock(return_value=httpx.Response(201, json={}))
            respx.get(url__regex=r".*/contents/").mock(return_value=httpx.Response(404))
            respx.put(url__regex=r".*/contents/").mock(return_value=httpx.Response(201, json={}))
            respx.post(url__regex=r".*/pulls$").mock(
                return_value=httpx.Response(
                    201,
                    json={"html_url": "https://github.com/acme/demo/pull/12", "number": 12},
                )
            )
            respx.post(url__regex=r".*/labels$").mock(
                return_value=httpx.Response(404, json={"message": "Resource not found"})
            )
            result = await service.open_fix(_request(failure.id), decision, decisions)

        assert result.ok is True
        assert result.label_applied is False
        assert result.label_error == "not_found"
        assert decision.pr_url == "https://github.com/acme/demo/pull/12"

    asyncio.run(scenario())


def test_dry_run_fix_records_the_simulated_url_without_writes(db_session: Session) -> None:
    """Dry-run still reads, then stores the URL it would have created."""
    failure, _created = FailureRepository(db_session).get_or_create(_failure(run_id=104))
    decisions = DecisionRepository(db_session)
    decision = decisions.add(_fix_decision(failure.id))
    service = PullRequestService(_client(dry_run=True))

    async def scenario() -> None:
        async with respx.mock:
            respx.get(url__regex=r".*/pulls").mock(return_value=httpx.Response(200, json=[]))
            respx.get(url__regex=r".*/git/ref/heads/").mock(return_value=httpx.Response(404))
            result = await service.open_fix(_request(failure.id), decision, decisions)
            assert _write_count() == 0

        assert result.ok is True
        assert result.dry_run is True
        assert result.url == f"dry-run://github/{_REPO}/pull/{_BRANCH}"
        assert decision.pr_url == result.url
        assert decision.dry_run is True
        assert decision.fix_branch == _BRANCH

    asyncio.run(scenario())


def test_issue_is_redacted_reused_and_skipped_in_dry_run(db_session: Session) -> None:
    """Issue text is capped, a second call reuses the issue, and dry-run does not POST."""
    failure, _created = FailureRepository(db_session).get_or_create(_failure(run_id=105))
    decisions = DecisionRepository(db_session)
    decision = decisions.add(
        DecisionCreate(
            failure_id=failure.id,
            action=DecisionAction.ESCALATE,
            category=FailureCategory.UNKNOWN,
            reason="needs a human",
            repo=_REPO,
            run_id=105,
        )
    )
    service = IssueService(_client())
    request = IssueRequest(
        repo=_REPO,
        failure_id=failure.id,
        category="unknown",
        confidence=0.2,
        reasoning=f"token {_SECRET}\n" + (("y" * 40) + "\n") * 1600,
        title="I" * 300,
    )
    phase = {"found": False}

    def search(_request: httpx.Request) -> httpx.Response:
        if phase["found"]:
            return httpx.Response(
                200,
                json={
                    "items": [{"html_url": "https://github.com/acme/demo/issues/3", "number": 3}]
                },
            )
        return httpx.Response(200, json={"items": []})

    async def scenario() -> None:
        async with respx.mock:
            looked = respx.get(url__regex=r".*/search/issues").mock(side_effect=search)
            created = respx.post(f"{_BASE}/repos/{_REPO}/issues").mock(
                return_value=httpx.Response(
                    201,
                    json={"html_url": "https://github.com/acme/demo/issues/3", "number": 3},
                )
            )
            respx.post(url__regex=r".*/labels$").mock(return_value=httpx.Response(200, json=[]))
            opened = await service.open_issue(request, decision, decisions)
            payload = json.loads(created.calls[0].request.content)
            phase["found"] = True
            again = await service.open_issue(request, decision, decisions)

        assert opened.ok is True
        assert decision.issue_url == "https://github.com/acme/demo/issues/3"
        assert _SECRET not in payload["title"] + payload["body"]
        assert "ghp_" not in payload["body"]
        assert len(payload["title"]) <= 120
        assert len(payload["body"]) < 60_000
        assert AI_NOTE in payload["body"]
        assert f"ci-heal-failure-{failure.id}" in payload["body"]
        assert f"ci-heal-failure-{failure.id}" in looked.calls[0].request.url.params["q"]
        assert again.reused is True
        assert created.call_count == 1

        dry = IssueService(_client(dry_run=True))
        async with respx.mock:
            respx.get(url__regex=r".*/search/issues").mock(
                return_value=httpx.Response(200, json={"items": []})
            )
            simulated = await dry.open_issue(request, decision, decisions)
            assert _write_count() == 0
        assert simulated.dry_run is True
        assert simulated.url == f"dry-run://github/{_REPO}/issues/{failure.id}"
        assert decision.issue_url == simulated.url

    asyncio.run(scenario())


def test_rerun_records_success_and_a_404_does_not(db_session: Session) -> None:
    """A rerun sets the flag. Dry-run sets it without a POST. A 404 leaves it clear."""
    failure, _created = FailureRepository(db_session).get_or_create(_failure(run_id=106))
    decisions = DecisionRepository(db_session)
    decision = decisions.add(
        DecisionCreate(
            failure_id=failure.id,
            action=DecisionAction.RETRY,
            category=FailureCategory.FLAKY_OR_NETWORK,
            reason="flake",
            repo=_REPO,
            run_id=106,
            expected_run_attempt=2,
        )
    )
    service = RerunService(_client())

    async def scenario() -> None:
        async with respx.mock:
            route = respx.post(url__regex=r".*/rerun-failed-jobs").mock(
                return_value=httpx.Response(201)
            )
            result = await service.request_rerun(
                repo=_REPO, run_id=106, decision=decision, decisions=decisions
            )
        assert result.ok is True
        assert route.called
        assert decision.rerun_requested is True

        fresh = decisions.add(
            DecisionCreate(
                failure_id=failure.id,
                action=DecisionAction.RETRY,
                category=FailureCategory.FLAKY_OR_NETWORK,
                reason="flake",
                repo=_REPO,
                run_id=106,
                expected_run_attempt=3,
            )
        )
        async with respx.mock:
            missed = await RerunService(_client(dry_run=True)).request_rerun(
                repo=_REPO, run_id=106, decision=fresh, decisions=decisions
            )
            assert respx.calls.call_count == 0
        assert missed.dry_run is True
        assert fresh.rerun_requested is True

        blocked = decisions.add(
            DecisionCreate(
                failure_id=failure.id,
                action=DecisionAction.RETRY,
                category=FailureCategory.FLAKY_OR_NETWORK,
                reason="flake",
                repo=_REPO,
                run_id=106,
                expected_run_attempt=4,
            )
        )
        async with respx.mock:
            respx.post(url__regex=r".*/rerun-failed-jobs").mock(
                return_value=httpx.Response(404, json={"message": "Not Found"})
            )
            missing = await service.request_rerun(
                repo=_REPO, run_id=106, decision=blocked, decisions=decisions
            )
        assert missing.ok is False
        assert missing.error == "not_found"
        assert blocked.rerun_requested is False

    asyncio.run(scenario())
