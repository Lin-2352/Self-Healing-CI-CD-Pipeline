"""Clean a model reply the way the n8n extract node does.

Qwen wraps reasoning in `<think>` tags, and models sometimes add a ```json fence even when
told not to. Both are removed before anyone tries to parse the reply as JSON.
"""

import re

_CLOSED_THINK = re.compile(r"<think>[\s\S]*?</think>")
_UNCLOSED_THINK = re.compile(r"^[\s\S]*?</think>")
_FENCE = re.compile(r"```(?:json)?")


def strip_model_text(text: str) -> str:
    """Drop thinking traces and markdown fences from a model reply.

    Args:
        text: The raw assistant text, already joined if it arrived as a stream.

    Returns:
        The text a parser should see.
    """
    text = _CLOSED_THINK.sub("", text)
    text = _UNCLOSED_THINK.sub("", text)
    return _FENCE.sub("", text).strip()
