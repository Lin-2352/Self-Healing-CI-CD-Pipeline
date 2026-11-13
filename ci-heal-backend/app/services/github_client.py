"""Async GitHub REST client.

Every write method returns before it touches the network when `dry_run` is set. That is
the only place a write can be turned into a log line. Reads still run. A commit or ref
write raises `GitHubWriteRefused` unless the branch starts with `ai-fix-` and is not the
default branch, `main`, or `master`.
"""

import asyncio
import base64
import io
import logging
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.services.github_text import BODY_CAP, TITLE_CAP
from app.services.secret_redactor import redact_outbound

logger = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"
_SUCCESS = frozenset({200, 201, 202, 204})
_REDIRECT = frozenset({301, 302, 303, 307, 308})
_MAX_PAGES = 20


class GitHubWriteRefused(Exception):
    """A commit or ref write targeted a branch this service must not touch.

    Raised as an exception, not a bare assert, so `python -O` cannot strip the check
    and the services can turn it into a result instead of crashing the orchestrator.
    """

    def __init__(self, branch: str) -> None:
        """Remember the branch that was refused.

        Args:
            branch: The ref name the caller tried to write.
        """
        self.branch = branch
        super().__init__(f"refusing to write branch {branch}")


@dataclass(frozen=True)
class CallResult:
    """One GitHub call, or the write a dry-run substituted for it."""

    ok: bool
    dry_run: bool = False
    error: str | None = None
    url: str | None = None
    sha: str | None = None
    text: str | None = None
    number: int | None = None
    data: dict[str, object] | None = None
    items: tuple[object, ...] = ()


@dataclass(frozen=True)
class ActionResult:
    """What a pull-request, issue, or rerun service did, or would have done."""

    ok: bool
    dry_run: bool = False
    reused: bool = False
    url: str | None = None
    branch: str | None = None
    error: str | None = None
    label_applied: bool = False
    label_error: str | None = None


class GitHubClient:
    """HTTP access to one GitHub API. The token leaves `SecretStr` only on the wire."""

    def __init__(
        self,
        *,
        token: SecretStr,
        base_url: str = "https://api.github.com",
        dry_run: bool = True,
        timeout: float = 30,
        max_retries: int = 2,
        max_file_bytes: int = 100_000,
        max_log_bytes: int = 1_000_000,
        default_branch: str = "main",
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Store connection settings. An empty token is allowed while dry-run is on.

        Args:
            token: Fine-grained personal access token.
            base_url: API root, without a trailing slash. `https://api.github.com` in production.
            dry_run: When true, write methods log and return without a request.
            timeout: Per-attempt timeout in seconds.
            max_retries: Extra attempts after the first, for 5xx and rate limits.
            max_file_bytes: Largest file body that will be decoded.
            max_log_bytes: Largest log archive or compare diff that will be read.
            default_branch: Ref writes to this name are refused.
            sleep: Wait used between retries. Tests pass a recorder.
            clock: Seconds-since-epoch source, used for `X-RateLimit-Reset`.
        """
        self._token = token
        self._base = base_url.rstrip("/")
        self._dry_run = dry_run
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_file_bytes = max_file_bytes
        self._max_log_bytes = max_log_bytes
        self._default_branch = default_branch
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.time

    def __repr__(self) -> str:
        """Return a representation that does not include the token."""
        return f"GitHubClient(base_url={self._base!r}, dry_run={self._dry_run})"

    @property
    def dry_run(self) -> bool:
        """True when write methods must not perform a network write."""
        return self._dry_run

    async def get_workflow_run(self, repo: str, run_id: int) -> CallResult:
        """Fetch one Actions workflow run.

        Args:
            repo: `owner/name`.
            run_id: GitHub workflow run id.

        Returns:
            The run JSON in `data`, or an error code.
        """
        response, err = await self._read("GET", f"/repos/{repo}/actions/runs/{run_id}")
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        payload = _json(response)
        if not isinstance(payload, dict):
            return CallResult(ok=False, error="invalid_response")
        return CallResult(ok=True, data=payload, sha=_as_str(payload.get("head_sha")))

    async def list_jobs(self, repo: str, run_id: int) -> CallResult:
        """Fetch every job for a workflow run, following `Link: rel=next`.

        Args:
            repo: `owner/name`.
            run_id: GitHub workflow run id.

        Returns:
            Job objects in `items`. Stops after 20 pages.
        """
        refused = _repo_error(repo)
        if refused:
            return refused
        jobs: list[object] = []
        url = f"/repos/{repo}/actions/runs/{run_id}/jobs"
        params: dict[str, str | int] | None = {"per_page": 100}
        for _page in range(_MAX_PAGES):
            response, err = await self._read("GET", url, params=params)
            if err or response is None:
                return _fail(err)
            code = _status_error(response)
            if code:
                return CallResult(ok=False, error=code)
            payload = _json(response)
            batch = payload.get("jobs") if isinstance(payload, dict) else None
            if not isinstance(batch, list):
                return CallResult(ok=False, error="invalid_response")
            jobs.extend(batch)
            nxt = _next_link(response.headers.get("link"))
            if not nxt:
                break
            url = nxt
            params = None
        return CallResult(ok=True, items=tuple(jobs))

    async def download_logs(self, repo: str, run_id: int) -> CallResult:
        """Download a workflow run's logs. GitHub answers with a redirect to a zip.

        The token is not forwarded to the redirect host.

        Args:
            repo: `owner/name`.
            run_id: GitHub workflow run id.

        Returns:
            Concatenated log text, or `too_large` / `bad_archive`.
        """
        response, err = await self._read("GET", f"/repos/{repo}/actions/runs/{run_id}/logs")
        if err or response is None:
            return _fail(err)
        payload = response.content
        if response.status_code in _REDIRECT:
            location = response.headers.get("location")
            if not location:
                return CallResult(ok=False, error="missing_redirect")
            follow, err = await self._read("GET", location, authenticated=False)
            if err or follow is None:
                return _fail(err)
            if follow.status_code not in _SUCCESS:
                return CallResult(ok=False, error=_status_error(follow) or "http_error")
            payload = follow.content
        elif response.status_code not in _SUCCESS:
            return CallResult(ok=False, error=_status_error(response) or "http_error")
        if len(payload) > self._max_log_bytes:
            return CallResult(ok=False, error="too_large")
        try:
            text = _unzip(payload, self._max_log_bytes)
        except zipfile.BadZipFile:
            return CallResult(ok=False, error="bad_archive")
        return CallResult(ok=True, text=text)

    async def compare(self, repo: str, base: str, head: str) -> CallResult:
        """Fetch the unified diff between two commits.

        Args:
            repo: `owner/name`.
            base: Base sha or ref.
            head: Head sha or ref.

        Returns:
            Diff text, or `too_large` when it exceeds the log cap.
        """
        path = f"/repos/{repo}/compare/{quote(base, safe='')}...{quote(head, safe='')}"
        response, err = await self._read(
            "GET", path, headers={"Accept": "application/vnd.github.diff"}
        )
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        if len(response.content) > self._max_log_bytes:
            return CallResult(ok=False, error="too_large")
        return CallResult(ok=True, text=response.text)

    async def get_file(self, repo: str, path: str, ref: str) -> CallResult:
        """Fetch one file's text at `ref`. A body over the size cap is not decoded.

        Args:
            repo: `owner/name`.
            path: Repo-relative path.
            ref: Commit sha or branch name.

        Returns:
            Decoded text and blob sha, or `not_found` / `too_large`.
        """
        if _bad_path(path):
            return CallResult(ok=False, error="invalid_path")
        response, err = await self._read(
            "GET",
            f"/repos/{repo}/contents/{quote(path, safe='/')}",
            params={"ref": ref},
        )
        if err or response is None:
            return _fail(err)
        if response.status_code == 404:
            return CallResult(ok=False, error="not_found")
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        payload = _json(response)
        if not isinstance(payload, dict) or payload.get("type") not in {None, "file"}:
            return CallResult(ok=False, error="invalid_response")
        size = _as_int(payload.get("size"))
        if size is not None and size > self._max_file_bytes:
            return CallResult(ok=False, error="too_large")
        encoded = payload.get("content")
        if not isinstance(encoded, str):
            return CallResult(ok=False, error="invalid_response")
        raw = base64.b64decode("".join(encoded.split()))
        if len(raw) > self._max_file_bytes:
            return CallResult(ok=False, error="too_large")
        return CallResult(
            ok=True, text=raw.decode("utf-8", errors="replace"), sha=_as_str(payload.get("sha"))
        )

    async def get_branch(self, repo: str, branch: str) -> CallResult:
        """Look up a branch ref. A missing branch is `not_found`, not an exception.

        Args:
            repo: `owner/name`.
            branch: Branch name, without `refs/heads/`.

        Returns:
            The commit sha the branch points at, or `not_found`.
        """
        quoted = quote(branch, safe="")
        response, err = await self._read("GET", f"/repos/{repo}/git/ref/heads/{quoted}")
        if err or response is None:
            return _fail(err)
        if response.status_code == 404:
            return CallResult(ok=False, error="not_found")
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        payload = _json(response)
        obj = payload.get("object") if isinstance(payload, dict) else None
        sha = obj.get("sha") if isinstance(obj, dict) else None
        return CallResult(ok=True, sha=_as_str(sha))

    async def find_open_pull(self, repo: str, branch: str) -> CallResult:
        """Find an open pull request whose head is `owner:branch`.

        Args:
            repo: `owner/name`.
            branch: Head branch name.

        Returns:
            `ok` with a URL when one exists, or `ok` and no URL when the list is empty.
        """
        refused = _repo_error(repo)
        if refused:
            return refused
        owner = repo.split("/", 1)[0]
        response, err = await self._read(
            "GET",
            f"/repos/{repo}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open"},
        )
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        payload = _json(response)
        if not isinstance(payload, list):
            return CallResult(ok=False, error="invalid_response")
        if not payload or not isinstance(payload[0], dict):
            return CallResult(ok=True)
        first = payload[0]
        return CallResult(
            ok=True, url=_as_str(first.get("html_url")), number=_as_int(first.get("number"))
        )

    async def find_issue(self, repo: str, marker: str) -> CallResult:
        """Find an issue whose body contains `marker`.

        Args:
            repo: `owner/name`.
            marker: The `ci-heal-failure-<id>` token written into the body.

        Returns:
            The issue URL when search returns a hit, or `ok` and no URL when it does not.
        """
        refused = _repo_error(repo)
        if refused:
            return refused
        query = f"repo:{repo} is:issue {marker} in:body"
        response, err = await self._read("GET", "/search/issues", params={"q": query})
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        payload = _json(response)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return CallResult(ok=False, error="invalid_response")
        if not items or not isinstance(items[0], dict):
            return CallResult(ok=True)
        first = items[0]
        return CallResult(
            ok=True, url=_as_str(first.get("html_url")), number=_as_int(first.get("number"))
        )

    async def create_branch(self, repo: str, branch: str, sha: str) -> CallResult:
        """Create `refs/heads/<branch>` at `sha`.

        Args:
            repo: `owner/name`.
            branch: Must be an allowed `ai-fix-*` branch.
            sha: Commit the new branch points at.

        Returns:
            A dry-run result, a success, or `already_exists` when GitHub returns 422 or 409.

        Raises:
            GitHubWriteRefused: `branch` is not an allowed fix branch.
        """
        self._refuse(branch)
        refused = _repo_error(repo)
        if refused:
            return refused
        if self._dry_run:
            logger.info("dry-run create_branch repo=%s branch=%s", repo, branch)
            return CallResult(ok=True, dry_run=True, sha=sha)
        response, err = await self._read(
            "POST",
            f"/repos/{repo}/git/refs",
            json_body={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        return CallResult(ok=True, sha=sha)

    async def commit_file(
        self,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        heal_attempt: int,
        blob_sha: str | None = None,
    ) -> CallResult:
        """Create or update one file on `branch` through the Contents API.

        Args:
            repo: `owner/name`.
            path: Repo-relative path.
            content: Full new file text.
            message: Commit message. `Heal-Attempt: N` is appended when missing.
            branch: Must be an allowed `ai-fix-*` branch.
            heal_attempt: Value written in the trailer.
            blob_sha: Current blob sha, required by GitHub when the file already exists.

        Returns:
            A dry-run result or a structured error. 404 and 422 do not raise.

        Raises:
            GitHubWriteRefused: `branch` is not an allowed fix branch.
        """
        self._refuse(branch)
        refused = _repo_error(repo)
        if refused:
            return refused
        if _bad_path(path):
            return CallResult(ok=False, error="invalid_path")
        if self._dry_run:
            logger.info("dry-run commit_file repo=%s branch=%s path=%s", repo, branch, path)
            return CallResult(ok=True, dry_run=True)
        body: dict[str, str] = {
            "message": _with_trailer(message, heal_attempt),
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if blob_sha:
            body["sha"] = blob_sha
        response, err = await self._read(
            "PUT",
            f"/repos/{repo}/contents/{quote(path, safe='/')}",
            json_body=body,
        )
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        return CallResult(ok=True)

    async def create_pull(
        self, repo: str, title: str, body: str, head: str, base: str
    ) -> CallResult:
        """Open a pull request. Title and body are redacted again here.

        Args:
            repo: `owner/name`.
            title: Pull-request title.
            body: Pull-request body.
            head: Must be an allowed `ai-fix-*` branch.
            base: Branch the pull request merges into. This is not a ref write.

        Returns:
            The new pull request URL and number, `already_exists`, or a dry-run result.

        Raises:
            GitHubWriteRefused: `head` is not an allowed fix branch.
        """
        self._refuse(head)
        refused = _repo_error(repo)
        if refused:
            return refused
        title, body = _bound_text(title, body)
        if self._dry_run:
            logger.info("dry-run create_pull repo=%s head=%s", repo, head)
            return CallResult(ok=True, dry_run=True)
        response, err = await self._read(
            "POST",
            f"/repos/{repo}/pulls",
            json_body={"title": title, "body": body, "head": head, "base": base},
        )
        return _created(response, err)

    async def create_issue(self, repo: str, title: str, body: str) -> CallResult:
        """Open an issue. Title and body are redacted again here.

        Args:
            repo: `owner/name`.
            title: Issue title.
            body: Issue body.

        Returns:
            The new issue URL and number, or a dry-run result.
        """
        refused = _repo_error(repo)
        if refused:
            return refused
        title, body = _bound_text(title, body)
        if self._dry_run:
            logger.info("dry-run create_issue repo=%s", repo)
            return CallResult(ok=True, dry_run=True)
        response, err = await self._read(
            "POST",
            f"/repos/{repo}/issues",
            json_body={"title": title, "body": body},
        )
        return _created(response, err)

    async def add_labels(self, repo: str, number: int, labels: list[str]) -> CallResult:
        """Add labels to an issue or pull request. Callers treat failure as non-fatal.

        Args:
            repo: `owner/name`.
            number: Issue or pull-request number.
            labels: Label names. `ai-fix` is the one this project applies.

        Returns:
            Success, or the status error. A missing label does not raise.
        """
        refused = _repo_error(repo)
        if refused:
            return refused
        if self._dry_run:
            logger.info("dry-run add_labels repo=%s number=%s", repo, number)
            return CallResult(ok=True, dry_run=True)
        response, err = await self._read(
            "POST",
            f"/repos/{repo}/issues/{number}/labels",
            json_body={"labels": labels},
        )
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        return CallResult(ok=True)

    async def rerun_failed_jobs(self, repo: str, run_id: int) -> CallResult:
        """Ask Actions to rerun the failed jobs of one workflow run.

        Args:
            repo: `owner/name`.
            run_id: GitHub workflow run id.

        Returns:
            Success, a dry-run result, or a status error such as `not_found`.
        """
        refused = _repo_error(repo)
        if refused:
            return refused
        if self._dry_run:
            logger.info("dry-run rerun_failed_jobs repo=%s run_id=%s", repo, run_id)
            return CallResult(ok=True, dry_run=True)
        response, err = await self._read(
            "POST", f"/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs"
        )
        if err or response is None:
            return _fail(err)
        code = _status_error(response)
        if code:
            return CallResult(ok=False, error=code)
        return CallResult(ok=True)

    def _refuse(self, branch: str) -> None:
        """Reject a ref write that could land on a protected branch.

        Args:
            branch: Proposed branch name.

        Raises:
            GitHubWriteRefused: The name is not an `ai-fix-*` branch we are allowed to write.
        """
        blocked = {"main", "master", self._default_branch}
        if (
            not isinstance(branch, str)
            or not branch.startswith("ai-fix-")
            or branch in blocked
            or "/" in branch
            or ".." in branch
            or any(char.isspace() for char in branch)
        ):
            raise GitHubWriteRefused(branch if isinstance(branch, str) else "")

    def _headers(self, *, authenticated: bool) -> dict[str, str]:
        """Build GitHub headers. The token is attached only for our own API host."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "ci-heal-backend",
        }
        token = self._token.get_secret_value()
        if authenticated and token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _read(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> tuple[httpx.Response | None, str | None]:
        """Perform one request, retrying 5xx responses and secondary rate limits.

        Args:
            method: HTTP method.
            url: Absolute URL, or a path under the API root.
            json_body: JSON object to send.
            params: Query string.
            headers: Extra headers. These override the defaults.
            authenticated: When false, the token is not sent. Used for log-zip redirects.

        Returns:
            The response and None, or None and `missing_token` / `timeout`.
        """
        if authenticated and not self._token.get_secret_value():
            return None, "missing_token"
        target = url if url.startswith(("http://", "https://")) else f"{self._base}{url}"
        merged = self._headers(authenticated=authenticated)
        if headers:
            merged.update(headers)
        last: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    response = await http.request(
                        method,
                        target,
                        headers=merged,
                        json=json_body,
                        params=params,
                        follow_redirects=False,
                    )
            except httpx.TimeoutException:
                if attempt >= self._max_retries:
                    return None, "timeout"
                await self._sleep(0.25 * (2**attempt))
                continue
            last = response
            delay = _retry_delay(response, self._clock(), attempt)
            if delay is None or attempt >= self._max_retries:
                return response, None
            await self._sleep(delay)
        return last, None


def build_github_client(settings: Settings) -> GitHubClient:
    """Build the client the app stores on `app.state`.

    An empty token does not prevent startup. A live read or write reports `missing_token`.

    Args:
        settings: Process settings.

    Returns:
        A client that honours `DRY_RUN`.
    """
    return GitHubClient(
        token=settings.github_token,
        base_url=settings.github_api_url,
        dry_run=settings.dry_run,
        timeout=settings.github_timeout,
        max_retries=settings.github_max_retries,
        max_file_bytes=settings.github_max_file_bytes,
        max_log_bytes=settings.github_max_log_bytes,
        default_branch=settings.github_default_branch,
    )


def _repo_error(repo: str) -> CallResult | None:
    """Return an error result when `repo` is not `owner/name`."""
    parts = repo.split("/")
    if len(parts) == 2 and all(part not in {"", ".", ".."} for part in parts):
        return None
    return CallResult(ok=False, error="invalid_repo")


def _bad_path(path: str) -> bool:
    """True when a contents path could escape the repository tree."""
    if not path or path.startswith("/") or "\\" in path:
        return True
    return any(part in {"", ".", ".."} for part in path.split("/"))


def _fail(err: str | None) -> CallResult:
    """A transport or token failure with no response to interpret."""
    return CallResult(ok=False, error=err or "http_error")


def _created(response: httpx.Response | None, err: str | None) -> CallResult:
    """Interpret a create-PR or create-issue response."""
    if err or response is None:
        return _fail(err)
    code = _status_error(response)
    if code:
        return CallResult(ok=False, error=code)
    payload = _json(response)
    if not isinstance(payload, dict):
        return CallResult(ok=False, error="invalid_response")
    return CallResult(
        ok=True,
        url=_as_str(payload.get("html_url")),
        number=_as_int(payload.get("number")),
    )


def _bound_text(title: str, body: str) -> tuple[str, str]:
    """Redact and cap text immediately before it is placed on the wire."""
    clean_title = str(redact_outbound(title).text)
    clean_body = str(redact_outbound(body).text)
    if len(clean_title) > TITLE_CAP:
        clean_title = clean_title[: TITLE_CAP - 3] + "..."
    if len(clean_body) > BODY_CAP:
        clean_body = clean_body[:BODY_CAP]
    return clean_title, clean_body


def _with_trailer(message: str, heal_attempt: int) -> str:
    """Append the heal-attempt trailer when the message does not already contain it."""
    trailer = f"Heal-Attempt: {heal_attempt}"
    text = message.rstrip()
    if trailer in text:
        return text if text.endswith("\n") else f"{text}\n"
    return f"{text}\n\n{trailer}\n"


def _status_error(response: httpx.Response) -> str | None:
    """Map a non-success status to a stable error code. Success returns None."""
    if response.status_code in _SUCCESS:
        return None
    return _error_code(response.status_code, _json(response))


def _error_code(status: int, payload: object) -> str:
    """Classify a GitHub error. 'already exists' on 409 or 422 is not fatal to the caller."""
    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message", "")).lower()
    if status == 404:
        return "not_found"
    if status in {409, 422} and "already exists" in message:
        return "already_exists"
    if status == 409:
        return "conflict"
    if status == 422:
        return "validation"
    if status in {401, 403}:
        return "forbidden"
    if status == 429:
        return "rate_limited"
    return f"http_{status}"


def _retry_delay(response: httpx.Response, now: float, attempt: int) -> float | None:
    """Seconds to wait before another try, or None when this status is final."""
    if not _retryable(response):
        return None
    hinted = _header_delay(response, now)
    if hinted is not None:
        return hinted
    return 0.25 * (2**attempt)


def _retryable(response: httpx.Response) -> bool:
    """True for 5xx, 429, and a 403 that carries a rate-limit hint."""
    status = response.status_code
    if status >= 500 or status == 429:
        return True
    if status != 403:
        return False
    if response.headers.get("retry-after"):
        return True
    return response.headers.get("x-ratelimit-remaining") == "0"


def _header_delay(response: httpx.Response, now: float) -> float | None:
    """Honour `Retry-After` or `X-RateLimit-Reset`. None means use the backoff instead."""
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError, IndexError, OverflowError):
                return None
            return max(0.0, parsed.timestamp() - now)
    reset = response.headers.get("x-ratelimit-reset")
    if not reset:
        return None
    try:
        return max(0.0, float(reset) - now)
    except ValueError:
        return None


def _next_link(header: str | None) -> str | None:
    """Return the URL in a GitHub `Link` header whose rel is `next`."""
    if not header:
        return None
    for part in header.split(","):
        section, _, rel = part.partition(";")
        if 'rel="next"' not in rel:
            continue
        url = section.strip()
        if url.startswith("<") and url.endswith(">"):
            return url[1:-1]
    return None


def _unzip(payload: bytes, cap: int) -> str:
    """Concatenate files in a log zip, stopping once `cap` decoded bytes are reached."""
    chunks: list[bytes] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if not name.endswith("/") and ".." not in name.split("/")
        )
        for name in names:
            data = archive.read(name)
            room = cap - total
            if room <= 0:
                break
            if len(data) > room:
                chunks.append(data[:room])
                break
            chunks.append(data)
            total += len(data)
    return b"\n".join(chunks).decode("utf-8", errors="replace")


def _json(response: httpx.Response) -> object:
    """Parse a JSON body. A non-JSON body becomes None and is not logged."""
    try:
        return response.json()
    except ValueError:
        return None


def _as_str(value: object) -> str | None:
    """Return `value` when it is a non-empty string."""
    if isinstance(value, str) and value:
        return value
    return None


def _as_int(value: object) -> int | None:
    """Return `value` when it is an int. Bool is rejected."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
