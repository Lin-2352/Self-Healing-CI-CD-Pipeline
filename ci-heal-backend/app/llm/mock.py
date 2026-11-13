"""A deterministic model client for development and tests.

Responses are scripted, one per `complete` call, in the order they were given. The last
scripted response is repeated if a test calls the client more times than it scripted, so a
single canned reply is enough for a loop. Every call is stored on `calls` for assertions.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.llm.base import ChatMessage


@dataclass(frozen=True)
class RecordedCall:
    """The arguments of one `complete` call, kept so a test can assert on them."""

    messages: tuple[ChatMessage, ...]
    temperature: float
    max_tokens: int
    enable_thinking: bool


class MockLLMClient:
    """Returns scripted replies and never touches the network."""

    def __init__(self, responses: Sequence[str] | None = None) -> None:
        """Store the replies `complete` will hand out, in order.

        Args:
            responses: One reply per call. Defaults to a single empty JSON object, which is
                what the app uses when no real model is configured.
        """
        self._responses = list(responses) if responses is not None else ["{}"]
        if not self._responses:
            raise ValueError("MockLLMClient needs at least one scripted response")
        self._index = 0
        self.calls: list[RecordedCall] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        enable_thinking: bool = False,
    ) -> str:
        """Return the next scripted reply and remember the call."""
        self.calls.append(
            RecordedCall(
                messages=tuple(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )
        )
        reply = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        return reply

    async def ping(self) -> bool:
        """Report that the mock provider is always available."""
        return True

    def __repr__(self) -> str:
        """Show how many replies are scripted, and nothing else."""
        return f"MockLLMClient(responses={len(self._responses)})"
