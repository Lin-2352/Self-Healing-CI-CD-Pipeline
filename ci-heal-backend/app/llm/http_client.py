"""OpenAI-compatible chat client.

The Kaggle tunnel, Groq and Gemini's OpenAI endpoint all speak this shape. Streaming is the
default because the Cloudflare tunnel closes a request that stays quiet for about two minutes.
Calls on one client run one at a time: the Kaggle server serves a single request, and a second
one would only sit in its queue.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from app.llm.base import ChatMessage, LLMError, LLMResponseError
from app.llm.text import strip_model_text


class _RetryableStatus(Exception):
    """A 5xx (including Cloudflare 524) that the caller may try again."""

    def __init__(self, status_code: int) -> None:
        """Remember the status. The message is the number only, never a response body."""
        self.status_code = status_code
        super().__init__(str(status_code))


class HttpLLMClient:
    """POST `{base}/v1/chat/completions` and return the assistant text."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str,
        timeout: float,
        stream: bool = True,
        max_retries: int = 2,
        deadline: float | None = None,
        backoff: float = 0.25,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Store connection settings. The key is a `SecretStr` and is never put in `repr`.

        Args:
            base_url: Server root, with or without a trailing `/v1`.
            api_key: Bearer token. Empty keys are rejected by the factory before this runs.
            model: The `model` field of the request. Kaggle expects `local`.
            timeout: Per-attempt timeout in seconds. Each attempt is also capped by the
                time left in `deadline`.
            stream: Send `stream: true` and read server-sent events.
            max_retries: Extra attempts after the first, only for 5xx, 524 and timeouts.
            deadline: Seconds allowed for the whole call, including every attempt and the
                waits between them. When omitted, the budget is one full timeout per
                attempt plus the planned backoff.
            backoff: Seconds to wait before the first retry. Each later wait doubles it.
            sleep: Wait function, replaced in tests so retries do not actually pause.
            clock: Monotonic clock, replaced in tests so a deadline can pass without waiting.
        """
        self._root = _server_root(base_url)
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._stream = stream
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep or _asyncio_sleep
        self._clock = clock or time.monotonic
        self._deadline = _call_deadline(
            timeout=timeout,
            max_retries=max_retries,
            backoff=backoff,
            deadline=deadline,
        )
        # One in-flight completion per client. The Kaggle server handles a single request.
        self._lock = asyncio.Lock()

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        enable_thinking: bool = False,
    ) -> str:
        """Send one completion, retrying only when another attempt could succeed."""
        async with self._lock:
            return await self._complete_with_retries(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )

    async def ping(self) -> bool:
        """Return True when `GET /v1/models` answers below 400. One attempt, no retry."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(min(5.0, self._timeout))) as http:
                response = await http.get(f"{self._root}/v1/models", headers=self._headers())
                return response.status_code < 400
        except Exception:
            return False

    def __repr__(self) -> str:
        """Show the model and host, never the API key."""
        return (
            f"HttpLLMClient(model={self._model!r}, base_url={self._root!r}, stream={self._stream})"
        )

    def _headers(self) -> dict[str, str]:
        """Build the request headers. The key leaves `SecretStr` only here."""
        return {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    async def _complete_with_retries(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        enable_thinking: bool,
    ) -> str:
        """Try the request, stopping when the call's deadline is exhausted.

        A retry only starts when the backoff fits in the time still left. The next attempt
        then uses whichever is smaller: the per-attempt timeout, or that remaining time.
        """
        body = _request_body(
            messages,
            model=self._model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=self._stream,
            enable_thinking=enable_thinking,
        )
        deadline_at = self._clock() + self._deadline
        last: Exception | None = None
        attempts_used = 0
        stopped_for_deadline = False
        for attempt in range(self._max_retries + 1):
            remaining = deadline_at - self._clock()
            if remaining <= 0:
                stopped_for_deadline = True
                break
            attempts_used += 1
            try:
                return await self._once(body, timeout=min(self._timeout, remaining))
            except (httpx.TimeoutException, _RetryableStatus) as exc:
                last = exc
                if attempt >= self._max_retries:
                    break
                wait = self._backoff * (2**attempt)
                if wait >= deadline_at - self._clock():
                    stopped_for_deadline = True
                    break
                await self._sleep(wait)
        if last is None:
            raise LLMError("LLM request failed before an attempt; deadline exhausted")
        detail = f"LLM request failed after {attempts_used} attempts ({type(last).__name__})"
        if stopped_for_deadline:
            detail = f"{detail}; deadline exhausted"
        raise LLMError(detail) from last

    async def _once(self, body: dict[str, Any], *, timeout: float) -> str:
        """Perform a single HTTP attempt and turn the response into assistant text."""
        url = f"{self._root}/v1/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as http:
            if self._stream:
                async with http.stream("POST", url, json=body, headers=self._headers()) as response:
                    _raise_for_status(response.status_code)
                    return await _assemble_stream(response)
            response = await http.post(url, json=body, headers=self._headers())
            _raise_for_status(response.status_code)
            return _assemble_json(response.json())


def _call_deadline(
    *, timeout: float, max_retries: int, backoff: float, deadline: float | None
) -> float:
    """Return the seconds allowed for one `complete` call, including retries and waits."""
    if deadline is None:
        waits = sum(backoff * (2**index) for index in range(max_retries))
        deadline = timeout * (max_retries + 1) + waits
    if deadline <= 0:
        raise ValueError("deadline must be positive")
    return deadline


def _server_root(base_url: str) -> str:
    """Normalise a base URL so both `https://host` and `https://host/v1` work."""
    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def _request_body(
    messages: Sequence[ChatMessage],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    stream: bool,
    enable_thinking: bool,
) -> dict[str, Any]:
    """Build an OpenAI chat-completions body. Thinking is sent only when asked for."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": message.role, "content": str(message.content)} for message in messages
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if enable_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": True}
    return body


def _raise_for_status(status_code: int) -> None:
    """Turn an HTTP error into either a retryable failure or a final one.

    4xx is final. 5xx, including Cloudflare's 524, is retryable.
    """
    if status_code >= 500:
        raise _RetryableStatus(status_code)
    if status_code >= 400:
        raise LLMResponseError(f"LLM request rejected with status {status_code}")


def _assemble_json(payload: object) -> str:
    """Read the assistant text from a non-streaming response body."""
    if not isinstance(payload, dict):
        raise LLMResponseError("LLM response was not a JSON object")
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError("LLM response had no message content") from exc
    if not isinstance(content, str):
        raise LLMResponseError("LLM message content was not text")
    return strip_model_text(content)


async def _assemble_stream(response: httpx.Response) -> str:
    """Join the `delta.content` pieces of a server-sent event stream."""
    parts: list[str] = []
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("malformed SSE chunk") from exc
        parts.append(_chunk_text(payload))
    if not any(parts):
        raise LLMResponseError("stream contained no content")
    return strip_model_text("".join(parts))


def _chunk_text(payload: object) -> str:
    """Return the text in one streamed chunk. An empty delta is a keep-alive, not an error."""
    if not isinstance(payload, dict):
        raise LLMResponseError("malformed SSE chunk")
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError("malformed SSE chunk") from exc
    if not isinstance(choice, dict):
        raise LLMResponseError("malformed SSE chunk")
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = delta.get("content")
    if content is None:
        content = message.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        raise LLMResponseError("SSE content was not text")
    return content


async def _asyncio_sleep(seconds: float) -> None:
    """Wait between retries. Tests pass their own function so they do not really wait."""
    await asyncio.sleep(seconds)
