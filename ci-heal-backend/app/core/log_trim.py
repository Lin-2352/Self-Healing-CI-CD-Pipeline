"""Trim a GitHub Actions job log down to the lines that explain the failure.

This is a direct port of `trimCiLog` in the n8n "Build AI Prompt" node. Keeping the same
rules means a log trimmed here matches what the Kaggle prompt was evaluated with.
"""

import re

LOG_MAX_LINES = 40
LOG_MAX_CHARS = 3000
LOG_UNAVAILABLE = "(log unavailable)"

_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CHECKOUT = re.compile(r"/home/runner/work/[^/\s]+/[^/\s]+/")
_ERROR = "##[error]"
_ENDGROUP = "##[endgroup]"


def trim_ci_log(text: str) -> str:
    """Return the failure region of a GitHub Actions log.

    Timestamps, ANSI colours and the `/home/runner/work/<repo>/<repo>/` prefix are stripped.
    The kept region runs from the last `##[endgroup]` before the first `##[error]` through
    that error line, then drops other `##[` directives. It is capped at 40 lines and 3000
    characters, keeping the end, because the error is at the bottom.

    Args:
        text: Raw job log. An empty string yields the unavailable marker.

    Returns:
        The trimmed log, or `(log unavailable)` when there is no `##[error]` line.
    """
    if not text.strip():
        return LOG_UNAVAILABLE

    lines = [_clean_line(line) for line in re.split(r"\r?\n", text)]
    error_at = next((i for i, line in enumerate(lines) if line.startswith(_ERROR)), -1)
    if error_at < 0:
        return LOG_UNAVAILABLE

    start = 0
    for index in range(error_at - 1, -1, -1):
        if lines[index].startswith(_ENDGROUP):
            start = index + 1
            break

    region = [_visible(line) for line in lines[start : error_at + 1]]
    region = [line for line in region if line is not None]
    while region and not region[0].strip():
        region.pop(0)
    region = region[-LOG_MAX_LINES:]

    trimmed = "\n".join(region)
    if len(trimmed) > LOG_MAX_CHARS:
        trimmed = trimmed[-LOG_MAX_CHARS:]
    return trimmed.strip() or LOG_UNAVAILABLE


def _clean_line(line: str) -> str:
    """Remove the runner timestamp, ANSI colours and the checkout prefix from one line."""
    line = _TIMESTAMP.sub("", line)
    line = _ANSI.sub("", line)
    return _CHECKOUT.sub("", line)


def _visible(line: str) -> str | None:
    """Drop workflow directives, and strip the `##[error]` marker from the error line."""
    if line.startswith(_ERROR):
        line = line[len(_ERROR) :]
    if line.startswith("##["):
        return None
    return line
