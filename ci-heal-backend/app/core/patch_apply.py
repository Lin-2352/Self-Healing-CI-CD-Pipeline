"""Apply one SEARCH/REPLACE proposal to a single file from the fixer context.

Model mistakes return a result. They do not raise. Matching is done on LF text so a CRLF
file still matches a model block that uses LF, and the original line ending is restored.

A path must be relative, free of `..`, and one of the files the caller provided. `.git/`,
`.env*`, and `.github/workflows/` are rejected. The workflow ban is the only one a caller
can turn off, via `ApplyLimits.allow_workflows`.

A patch that weakens tests is rejected: a new skip or xfail marker, a deleted test
function, or more assert lines removed than added. Changing an assert in place is kept.
"""

import ast
import difflib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.services.secret_redactor import redact

_SEARCH = "<<<<<<< SEARCH"
_DIVIDER = "======="
_REPLACE = ">>>>>>> REPLACE"


class ApplyError(StrEnum):
    """Why a proposal was not applied. Stable values, stored on the candidate row."""

    MALFORMED_EDITS = "malformed_edits"
    EMPTY_SEARCH = "empty_search"
    SEARCH_NOT_FOUND = "search_not_found"
    SEARCH_AMBIGUOUS = "search_ambiguous"
    NO_CHANGE = "no_change"
    PATH_TRAVERSAL = "path_traversal"
    ABSOLUTE_PATH = "absolute_path"
    FORBIDDEN_PATH = "forbidden_path"
    PATH_NOT_IN_CONTEXT = "path_not_in_context"
    TOO_MANY_EDITS = "too_many_edits"
    TOO_MANY_CHANGED_LINES = "too_many_changed_lines"
    SECRET_IN_REPLACEMENT = "secret_in_replacement"
    PYTHON_SYNTAX = "python_syntax"
    WEAKENS_TESTS = "weakens_tests"


@dataclass(frozen=True)
class ApplyLimits:
    """Caps for one candidate. Built from settings at the edge and passed in."""

    max_edits: int = 5
    max_changed_lines: int = 50
    allow_workflows: bool = False


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of one proposal. `new_content` is set only when the patch is accepted."""

    applied_ok: bool
    error: ApplyError | None
    new_content: str | None
    diff: str | None


def apply_patch(
    *,
    file_path: str,
    edits: str,
    files: Mapping[str, str],
    limits: ApplyLimits | None = None,
) -> ApplyResult:
    """Apply `edits` to `file_path` inside `files`.

    Args:
        file_path: The single relative path the model named.
        edits: SEARCH/REPLACE blocks. More than one block is applied in order.
        files: Path to original file text. This is the real content, not the redacted prompt.
        limits: Edit count, changed-line count, and whether workflow files are editable.

    Returns:
        A result. `applied_ok` is false for every model mistake, including a search that
        misses, matches twice, does not change the file, or weakens tests.
    """
    caps = limits or ApplyLimits()
    path, path_error = _normalise_path(file_path)
    if path_error is not None or path is None:
        return _reject(path_error or ApplyError.PATH_TRAVERSAL)
    if _is_forbidden(path, allow_workflows=caps.allow_workflows):
        return _reject(ApplyError.FORBIDDEN_PATH)
    context = {_normalise_key(key): value for key, value in files.items()}
    original = context.get(path)
    if original is None:
        return _reject(ApplyError.PATH_NOT_IN_CONTEXT)

    blocks, parse_error = _parse_blocks(edits)
    if parse_error is not None or blocks is None:
        return _reject(parse_error or ApplyError.MALFORMED_EDITS)
    if len(blocks) > caps.max_edits:
        return _reject(ApplyError.TOO_MANY_EDITS)
    for _search, replacement in blocks:
        if redact(replacement).count > 0:
            return _reject(ApplyError.SECRET_IN_REPLACEMENT)

    updated, apply_error = _apply_blocks(original, blocks)
    if apply_error is not None or updated is None:
        return _reject(apply_error or ApplyError.NO_CHANGE)

    diff = _unified(path, _to_lf(original), _to_lf(updated))
    if _changed_lines(diff) > caps.max_changed_lines:
        return ApplyResult(False, ApplyError.TOO_MANY_CHANGED_LINES, None, diff)
    if path.endswith(".py") and not _parses(updated):
        return ApplyResult(False, ApplyError.PYTHON_SYNTAX, None, diff)
    if _weakens_tests(path, original, updated, diff):
        return ApplyResult(False, ApplyError.WEAKENS_TESTS, None, diff)
    return ApplyResult(True, None, updated, diff)


def _reject(error: ApplyError) -> ApplyResult:
    """A model mistake with no patched file and no diff."""
    return ApplyResult(False, error, None, None)


def _normalise_path(file_path: str) -> tuple[str | None, ApplyError | None]:
    """Return a repo-relative path, or the reason it cannot be used."""
    if not isinstance(file_path, str):
        return None, ApplyError.PATH_TRAVERSAL
    raw = file_path.strip().replace("\\", "/")
    if raw == "":
        return None, ApplyError.PATH_TRAVERSAL
    if raw.startswith("/") or raw.startswith("//") or _has_drive(raw):
        return None, ApplyError.ABSOLUTE_PATH
    parts = [part for part in raw.split("/") if part != "."]
    if not parts or any(part == "" for part in parts):
        return None, ApplyError.PATH_TRAVERSAL
    if any(part == ".." for part in parts):
        return None, ApplyError.PATH_TRAVERSAL
    return "/".join(parts), None


def _normalise_key(file_path: str) -> str:
    """Normalise a context key. An unusable key cannot match a model path."""
    path, error = _normalise_path(file_path)
    if error is not None or path is None:
        return ""
    return path


def _has_drive(path: str) -> bool:
    """True for `C:...` Windows paths."""
    return len(path) >= 2 and path[0].isalpha() and path[1] == ":"


def _is_forbidden(path: str, *, allow_workflows: bool) -> bool:
    """True for `.git/`, `.env*`, and workflow files when those stay blocked."""
    parts = path.split("/")
    if ".git" in parts:
        return True
    if any(part == ".env" or part.startswith(".env") for part in parts):
        return True
    workflow = len(parts) >= 2 and parts[0] == ".github" and parts[1] == "workflows"
    return workflow and not allow_workflows


def _parse_blocks(edits: str) -> tuple[list[tuple[str, str]] | None, ApplyError | None]:
    """Split edits into `(search, replacement)` pairs. Markers must be their own lines."""
    if not isinstance(edits, str) or edits.strip() == "":
        return None, ApplyError.MALFORMED_EDITS
    lines = _to_lf(edits).split("\n")
    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "":
            index += 1
            continue
        if lines[index].strip() != _SEARCH:
            return None, ApplyError.MALFORMED_EDITS
        index += 1
        search, index, broken = _take_until(lines, index, _DIVIDER)
        if broken:
            return None, ApplyError.MALFORMED_EDITS
        replacement, index, broken = _take_until(lines, index, _REPLACE)
        if broken:
            return None, ApplyError.MALFORMED_EDITS
        if search.strip() == "":
            return None, ApplyError.EMPTY_SEARCH
        blocks.append((search, replacement))
    if not blocks:
        return None, ApplyError.MALFORMED_EDITS
    return blocks, None


def _take_until(lines: list[str], index: int, marker: str) -> tuple[str, int, bool]:
    """Collect lines until `marker`. The third value is true when the marker is missing."""
    collected: list[str] = []
    while index < len(lines) and lines[index].strip() != marker:
        collected.append(lines[index])
        index += 1
    if index >= len(lines):
        return "", index, True
    return "\n".join(collected), index + 1, False


def _apply_blocks(
    original: str, blocks: list[tuple[str, str]]
) -> tuple[str | None, ApplyError | None]:
    """Replace each search exactly once. The file's own newline is written back."""
    newline = "\r\n" if "\r\n" in original else "\n"
    current = _to_lf(original)
    for search, replacement in blocks:
        needle = _to_lf(search)
        found = _count(current, needle)
        if found == 0:
            return None, ApplyError.SEARCH_NOT_FOUND
        if found > 1:
            return None, ApplyError.SEARCH_AMBIGUOUS
        current = current.replace(needle, _to_lf(replacement), 1)
    if current == _to_lf(original):
        return None, ApplyError.NO_CHANGE
    restored = current if newline == "\n" else current.replace("\n", "\r\n")
    return restored, None


def _count(haystack: str, needle: str) -> int:
    """Count non-overlapping occurrences. An empty needle is never a match."""
    if needle == "":
        return 0
    found = 0
    start = 0
    while True:
        at = haystack.find(needle, start)
        if at < 0:
            return found
        found += 1
        if found > 1:
            return found
        start = at + len(needle)


def _to_lf(text: str) -> str:
    """Collapse CRLF and lone CR to LF so matching does not depend on the model."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _unified(path: str, before: str, after: str) -> str:
    """Build a unified diff from LF text. Agreement strips the headers again later."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lines)


def _changed_lines(diff: str) -> int:
    """Count added and removed lines, ignoring the `---` and `+++` headers."""
    changed = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            changed += 1
    return changed


_SKIP_MARKERS = (
    "pytest.mark.skip",
    "xfail",
    ".skip(",
    "@Ignore",
    "@Disabled",
)
_TEST_DEF = re.compile(
    r"(?:async\s+def|def|fun|func|function|void)\s+(test_\w+)",
)
_ASSERT_LINE = re.compile(r"assert\b|\.assert|assert[A-Z]")


def _weakens_tests(path: str, original: str, updated: str, diff: str) -> bool:
    """True when the patch skips, deletes, or strips assertions from tests."""
    if any(updated.count(marker) > original.count(marker) for marker in _SKIP_MARKERS):
        return True
    if _deleted_tests(path, original, updated):
        return True
    removed, added = _assert_delta(diff)
    return removed > added


def _deleted_tests(path: str, original: str, updated: str) -> bool:
    """True when a test that existed before the patch is gone afterwards."""
    before = _test_names(path, original)
    after = _test_names(path, updated)
    return bool(before - after)


def _test_names(path: str, source: str) -> set[str]:
    """Names of test functions. Python uses the AST; other files use a definition regex."""
    if path.endswith(".py"):
        parsed = _python_test_names(source)
        if parsed is not None:
            return parsed
    return set(_TEST_DEF.findall(source))


def _python_test_names(source: str) -> set[str] | None:
    """Return `test_*` function names, or None when the source is not valid Python."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            names.add(node.name)
    return names


def _assert_delta(diff: str) -> tuple[int, int]:
    """Count assert lines removed and added, ignoring diff headers."""
    removed = 0
    added = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "@@", "diff ")):
            continue
        if not line or line[0] not in "+-":
            continue
        if not _ASSERT_LINE.search(line[1:]):
            continue
        if line.startswith("+"):
            added += 1
        else:
            removed += 1
    return removed, added


def _parses(source: str) -> bool:
    """True when `source` is a Python module. `ast.parse` does not execute it."""
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True
