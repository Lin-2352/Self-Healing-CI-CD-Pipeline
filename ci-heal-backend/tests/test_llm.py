"""The model clients: scripted mock, and an HTTP client exercised with respx."""

import asyncio
import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.config import Settings
from app.llm.base import ChatMessage, LLMError, LLMResponseError
from app.llm.http_client import HttpLLMClient
from app.llm.mock import MockLLMClient
from app.main import create_app
from app.services.secret_redactor import redact

_URL = "https://llm.test/v1/chat/completions"

_SECRETS: dict[str, SecretStr] = {
    "github_webhook_secret": SecretStr("hook"),
    "n8n_shared_token": SecretStr("n8n"),
    "api_token": SecretStr("api"),
}


def _message(text: str = "hello") -> ChatMessage:
    return ChatMessage(role="user", content=redact(text).text)


def _sse(*pieces: str) -> bytes:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n"
        for piece in pieces
    ]
    lines.append("data: [DONE]\n")
    return "".join(lines).encode()


def _client(**overrides: object) -> HttpLLMClient:
    settings: dict[str, object] = {
        "base_url": "https://llm.test/v1",
        "api_key": SecretStr("super-secret-key"),
        "model": "local",
        "timeout": 5,
        "stream": True,
        "max_retries": 1,
        "backoff": 0.25,
        "sleep": _no_sleep,
    }
    settings.update(overrides)
    return HttpLLMClient(**settings)  # type: ignore[arg-type]


async def _no_sleep(_seconds: float) -> None:
    return None


def test_plain_strings_cannot_be_sent_to_a_model() -> None:
    with pytest.raises(TypeError):
        ChatMessage(role="user", content="still a secret")  # type: ignore[arg-type]


def test_mock_client_is_scripted_and_records_calls() -> None:
    async def scenario() -> None:
        client = MockLLMClient(["first", "second"])
        assert await client.ping() is True
        assert await client.complete([_message()], temperature=0.1, max_tokens=8) == "first"
        assert (
            await client.complete(
                [_message("again")], temperature=0.7, max_tokens=16, enable_thinking=True
            )
            == "second"
        )
        # A third call repeats the last scripted reply rather than inventing one.
        assert await client.complete([_message()], temperature=0, max_tokens=1) == "second"
        assert [call.temperature for call in client.calls] == [0.1, 0.7, 0]
        assert client.calls[1].enable_thinking is True
        assert "again" in client.calls[1].messages[0].content

    asyncio.run(scenario())


def test_streaming_strips_think_blocks_and_fences() -> None:
    async def scenario() -> None:
        client = _client()
        async with respx.mock:
            route = respx.post(_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=_sse("<thi", 'nk>hidden</think>```json\n{"ok":true}\n```'),
                )
            )
            text = await client.complete(
                [_message()], temperature=0.1, max_tokens=32, enable_thinking=True
            )

        assert text == '{"ok":true}'
        body = json.loads(route.calls[0].request.content)
        assert body["stream"] is True
        assert body["model"] == "local"
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert route.calls[0].request.headers["authorization"] == "Bearer super-secret-key"
        assert "super-secret-key" not in repr(client)

    asyncio.run(scenario())


def test_retries_a_524_and_does_not_retry_a_401() -> None:
    async def scenario() -> None:
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            delays.append(seconds)

        client = _client(sleep=sleep)
        async with respx.mock:
            route = respx.post(_URL).mock(
                side_effect=[httpx.Response(524), httpx.Response(200, content=_sse("fixed"))]
            )
            assert await client.complete([_message()], temperature=0, max_tokens=8) == "fixed"
        assert route.call_count == 2
        assert delays == [0.25]

        client = _client(max_retries=3)
        async with respx.mock:
            route = respx.post(_URL).mock(return_value=httpx.Response(401))
            with pytest.raises(LLMResponseError, match="401"):
                await client.complete([_message()], temperature=0, max_tokens=8)
        assert route.call_count == 1

    asyncio.run(scenario())


def test_timeout_is_retried_and_a_malformed_stream_is_not() -> None:
    async def scenario() -> None:
        client = _client()
        async with respx.mock:
            route = respx.post(_URL).mock(
                side_effect=[
                    httpx.TimeoutException("timed out"),
                    httpx.TimeoutException("timed out"),
                ]
            )
            with pytest.raises(LLMError, match="TimeoutException"):
                await client.complete([_message()], temperature=0, max_tokens=8)
        assert route.call_count == 2

        client = _client(max_retries=3)
        async with respx.mock:
            route = respx.post(_URL).mock(
                return_value=httpx.Response(200, content=b"data: {not-json}\n\n")
            )
            with pytest.raises(LLMResponseError, match="malformed"):
                await client.complete([_message()], temperature=0, max_tokens=8)
        assert route.call_count == 1

    asyncio.run(scenario())


def test_calls_on_one_client_do_not_overlap() -> None:
    async def scenario() -> None:
        inflight = {"now": 0, "max": 0}

        async def handler(_request: httpx.Request) -> httpx.Response:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
            await asyncio.sleep(0.05)
            inflight["now"] -= 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        client = _client(stream=False)
        async with respx.mock:
            respx.post(_URL).mock(side_effect=handler)
            await asyncio.gather(
                client.complete([_message()], temperature=0, max_tokens=4),
                client.complete([_message("other")], temperature=0, max_tokens=4),
            )
        assert inflight["max"] == 1

    asyncio.run(scenario())


def test_retries_stop_when_the_call_deadline_is_exhausted() -> None:
    async def scenario() -> None:
        now = {"t": 0.0}

        def clock() -> float:
            return now["t"]

        async def sleep(seconds: float) -> None:
            now["t"] += seconds

        def fail(_request: httpx.Request) -> httpx.Response:
            now["t"] += 8
            raise httpx.TimeoutException("timed out")

        # The attempt uses 8s of a 10s budget, and the 3s backoff does not fit.
        # max_retries would otherwise allow five more attempts.
        client = _client(
            timeout=30,
            max_retries=5,
            deadline=10,
            backoff=3,
            clock=clock,
            sleep=sleep,
        )
        async with respx.mock:
            route = respx.post(_URL).mock(side_effect=fail)
            with pytest.raises(LLMError, match="deadline exhausted"):
                await client.complete([_message()], temperature=0, max_tokens=8)
        assert route.call_count == 1

        now["t"] = 0.0
        client = _client(
            timeout=30,
            max_retries=5,
            deadline=20,
            backoff=3,
            clock=clock,
            sleep=sleep,
        )
        async with respx.mock:
            route = respx.post(_URL).mock(side_effect=fail)
            with pytest.raises(LLMError, match="deadline exhausted"):
                await client.complete([_message()], temperature=0, max_tokens=8)
        # 8s attempt + 3s backoff + 8s attempt = 19s, and the next 6s backoff does not fit.
        assert route.call_count == 2

    asyncio.run(scenario())


def test_http_provider_without_a_key_fails_at_startup() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        **_SECRETS,
        database_url="sqlite:///:memory:",
        fixer_llm_provider="http",
        fixer_llm_base_url="https://llm.test",
    )

    with pytest.raises(ValueError, match="FIXER_LLM_API_KEY") as raised:
        create_app(settings)

    assert "super-secret" not in str(raised.value)
