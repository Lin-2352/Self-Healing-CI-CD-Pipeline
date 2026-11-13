"""The model interface shared by the mock client and the HTTP client."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.core.redacted import RedactedText


class LLMError(Exception):
    """A model call failed. The message never includes a request header or an API key."""


class LLMResponseError(LLMError):
    """The provider answered, but the body was not a usable chat completion.

    This covers 4xx responses and a 200 whose body we cannot parse. Those are not retried:
    repeating a rejected or nonsensical request will not make it succeed.
    """


@dataclass(frozen=True)
class ChatMessage:
    """One turn of a chat completion. The content must already be redacted."""

    role: Literal["system", "user", "assistant"]
    content: RedactedText

    def __post_init__(self) -> None:
        """Refuse a plain string so an unredacted log cannot be wrapped by accident."""
        if type(self.content) is not RedactedText:
            raise TypeError(
                "LLM message content must be RedactedText. "
                "Use RedactedText.trusted() for prompt text, or redact()/prepare_log() "
                "for logs, diffs, and model output."
            )


@runtime_checkable
class LLMClient(Protocol):
    """A chat model. `complete` is sequential for the HTTP client: one call at a time."""

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        enable_thinking: bool = False,
    ) -> str:
        """Return the assistant text for `messages`.

        Args:
            messages: The conversation. Every content value is `RedactedText`.
            temperature: Sampling temperature. The fixer uses a higher one than the classifier.
            max_tokens: Upper bound on the completion.
            enable_thinking: Ask a reasoning model to think. The Kaggle server honours this
                on retries only; other providers ignore the extra field.

        Returns:
            The assistant text, with thinking traces and code fences removed.
        """
        ...

    async def ping(self) -> bool:
        """Return True when the provider answers a cheap readiness check."""
        ...
