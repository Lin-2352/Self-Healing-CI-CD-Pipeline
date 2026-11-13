"""Replace secrets in text before it is stored or sent to a model.

The matchers are format-specific on purpose. A generic "long random string" rule would also
eat commit SHAs and base64 file contents, and the classifier needs those. A second pass over
already-redacted text changes nothing.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.core.log_trim import trim_ci_log
from app.core.redacted import RedactedText

_SKIPPED_VALUES = frozenset({"", "null", "none", "nil", "undefined", "true", "false"})

# One identifier segment is the keyword, so `tokenization: 1` is left alone while
# `DB_PASSWORD`, `API_KEY` and `"password"` still match. Longer keywords come first.
_SECRET_NAME = (
    r"(?:[A-Za-z0-9]+_)*"
    r"(?:PASSWORD|PASSWD|PRIVATE_KEY|CREDENTIALS|ACCESS_KEY|ACCOUNT_KEY|ACCOUNTKEY"
    r"|API_KEY|APIKEY|TOKEN|SECRET|PWD)"
    r"(?:_[A-Za-z0-9]+)*"
)


@dataclass(frozen=True)
class RedactionResult:
    """Text with secrets replaced, plus how many replacements were made."""

    text: RedactedText
    count: int


@dataclass(frozen=True)
class PreparedContext:
    """A failure's log, diff and files after redaction. Safe to store or send to a model."""

    log: RedactedText
    diff: RedactedText
    files: dict[str, RedactedText]
    redaction_count: int


def redact(text: str | None) -> RedactionResult:
    """Replace recognised secrets with `[REDACTED:<KIND>]` placeholders.

    This, and the helpers below, are the path that produces `RedactedText` from untrusted
    input. Static prompt text uses `RedactedText.trusted` instead. The count is what
    `failures.redaction_count` stores.

    Args:
        text: Any log, diff, file body or model reply. None is treated as empty.

    Returns:
        The redacted text and the number of secrets removed.
    """
    if not text:
        return RedactionResult(text=RedactedText._from_redactor(""), count=0)

    total = 0
    for pattern, replace in _RULES:
        text, found = _apply(pattern, text, replace)
        total += found
    return RedactionResult(text=RedactedText._from_redactor(text), count=total)


def redact_secrets(text: str | None) -> RedactionResult:
    """Same as `redact`. Kept so earlier callers and tests stay readable."""
    return redact(text)


def redact_code(text: str | None) -> RedactionResult:
    """Redact a diff or a source file. Same rules as `redact`, with no log trimming."""
    return redact(text)


def redact_outbound(text: str | None) -> RedactionResult:
    """Redact text the model wrote before it is posted to GitHub.

    A pull-request title, pull-request body or issue body can echo a token the model saw
    or invented. The PR and issue services should send only this result.
    """
    return redact(text)


def prepare_log(text: str | None) -> tuple[RedactedText, int]:
    """Redact a whole CI log, then trim it to the failure region.

    Redaction runs first, on the full text. Trimming first would drop secrets that sit
    outside the error region from the count, and would let a secret inside the region
    reach the model if a caller forgot the second step.

    Args:
        text: Raw job log.

    Returns:
        The trimmed redacted log, and the number of secrets removed from the full text.
    """
    redacted = redact(text)
    return RedactedText._from_redactor(trim_ci_log(redacted.text)), redacted.count


def prepare_context(
    *,
    log: str | None,
    diff: str | None = None,
    files: dict[str, str] | None = None,
) -> PreparedContext:
    """Redact the log, the diff and every file that will be shown to the model.

    The log is trimmed after redaction. The diff and files are redacted in full, because
    a secret is often introduced by the change itself and is not in the error region.

    Args:
        log: Raw job log.
        diff: Unified diff for the failing commit. None is treated as empty.
        files: Path to file contents for the files the fixer may edit.

    Returns:
        Redacted pieces and the total number of secrets removed across all of them.
    """
    redacted_log, log_count = prepare_log(log)
    redacted_diff = redact_code(diff)
    redacted_files = {path: redact_code(body) for path, body in (files or {}).items()}
    total = log_count + redacted_diff.count + sum(item.count for item in redacted_files.values())
    return PreparedContext(
        log=redacted_log,
        diff=redacted_diff.text,
        files={path: item.text for path, item in redacted_files.items()},
        redaction_count=total,
    )


def _apply(
    pattern: re.Pattern[str],
    text: str,
    replace: Callable[[re.Match[str]], str],
) -> tuple[str, int]:
    """Apply one matcher, counting only replacements that actually change the text."""
    pieces: list[str] = []
    cursor = 0
    found = 0
    for match in pattern.finditer(text):
        replacement = replace(match)
        pieces.append(text[cursor : match.start()])
        pieces.append(replacement)
        if replacement != match.group(0):
            found += 1
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces), found


def _placeholder(kind: str) -> Callable[[re.Match[str]], str]:
    """Build a replacer that swaps the whole match for one placeholder."""

    def replace(_match: re.Match[str]) -> str:
        """Return the placeholder for this secret kind."""
        return f"[REDACTED:{kind}]"

    return replace


def _unquote(raw: str) -> str:
    """Drop one matching pair of quotes around a value."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _is_benign(value: str) -> bool:
    """True when a captured value is empty, a null-like word, a mask, or already redacted."""
    if value.lower() in _SKIPPED_VALUES or value.startswith("[REDACTED"):
        return True
    return bool(value) and set(value) <= {"*"}


def _redact_env(match: re.Match[str]) -> str:
    """Keep the variable name and replace its value, unless the value is not a secret."""
    if _is_benign(_unquote(match.group("value"))):
        return match.group(0)
    return f"{match.group('quoted_name')}{match.group('sep')}[REDACTED:ENV_SECRET]"


def _redact_cli(match: re.Match[str]) -> str:
    """Keep a `--token` style flag and replace the value that follows it."""
    if _is_benign(_unquote(match.group("value"))):
        return match.group(0)
    return f"{match.group('flag')}{match.group('sep')}[REDACTED:CLI_SECRET]"


def _redact_url(match: re.Match[str]) -> str:
    """Drop the user:password part of a URL and keep the scheme and host."""
    return f"{match.group(1)}[REDACTED:URL_CREDENTIALS]@"


def _redact_bearer(match: re.Match[str]) -> str:
    """Replace a bearer token, leaving the header word so the log still reads."""
    if _is_benign(match.group(2)):
        return match.group(0)
    return f"{match.group(1)}[REDACTED:BEARER]"


def _redact_basic(match: re.Match[str]) -> str:
    """Replace a Basic credential, leaving the header word so the log still reads."""
    if _is_benign(match.group(2)):
        return match.group(0)
    return f"{match.group(1)}[REDACTED:BASIC_AUTH]"


# Specific formats come first so `API_KEY=ghp_...` is labelled a GitHub token, not a
# generic env value. The env rule runs last and ignores values that are already placeholders.
_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
        _placeholder("PRIVATE_KEY"),
    ),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        _placeholder("JWT"),
    ),
    (
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        _placeholder("GITHUB_TOKEN"),
    ),
    (
        re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
        _placeholder("GITHUB_TOKEN"),
    ),
    (
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        _placeholder("AWS_ACCESS_KEY"),
    ),
    (
        re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
        _placeholder("SLACK_TOKEN"),
    ),
    (
        re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"),
        _placeholder("STRIPE_KEY"),
    ),
    (
        re.compile(r"AIza[0-9A-Za-z_-]{35}"),
        _placeholder("GOOGLE_API_KEY"),
    ),
    (
        re.compile(r"npm_[A-Za-z0-9]{20,}"),
        _placeholder("NPM_TOKEN"),
    ),
    (
        re.compile(r"pypi-[A-Za-z0-9_\-]{16,}"),
        _placeholder("PYPI_TOKEN"),
    ),
    (
        re.compile(r"dckr_pat_[A-Za-z0-9_\-]{20,}"),
        _placeholder("DOCKER_TOKEN"),
    ),
    (
        re.compile(r"SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
        _placeholder("SENDGRID_KEY"),
    ),
    (
        re.compile(
            r"DefaultEndpointsProtocol=https?;AccountName=[^;\s]+;AccountKey=[^;\s]+"
            r"(?:;EndpointSuffix=[^\s]+)?"
        ),
        _placeholder("AZURE_STORAGE_KEY"),
    ),
    (
        re.compile(r"([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@", re.IGNORECASE),
        _redact_url,
    ),
    (
        re.compile(r"(?i)(authorization[ \t]*:[ \t]*basic[ \t]+|basic[ \t]+)([A-Za-z0-9+/=]{8,})"),
        _redact_basic,
    ),
    (
        re.compile(
            r"(?i)(authorization[ \t]*:[ \t]*bearer[ \t]+|bearer[ \t]+)([A-Za-z0-9\-._~+/=]{8,})"
        ),
        _redact_bearer,
    ),
    (
        re.compile(
            r"(?ix)"
            r"(?P<flag>--(?:token|password|passwd|pwd|secret|api-key|api_key"
            r"|access-key|access_key|private-key|private_key|credentials))"
            r"(?P<sep>=|[ \t]+)"
            r"(?P<value>\"[^\"]*\"|'[^']*'|\S+)"
        ),
        _redact_cli,
    ),
    (
        re.compile(
            # Spaces and tabs only. A newline must not count, or `API_KEY=` eats the next line.
            r"(?ix)"
            r"(?P<quoted_name>(?P<q>[\"']?)(?P<name>" + _SECRET_NAME + r")(?P=q))"
            r"(?P<sep>[ \t]*[=:][ \t]*)"
            r"(?P<value>\"[^\"]*\"|'[^']*'|\S+)"
        ),
        _redact_env,
    ),
]
