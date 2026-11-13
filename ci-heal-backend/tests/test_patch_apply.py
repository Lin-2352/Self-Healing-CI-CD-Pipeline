"""SEARCH/REPLACE application, path rules, and the checks that reject a model mistake."""

import pytest

from app.core.patch_apply import ApplyError, ApplyLimits, apply_patch

_FILE = "def value():\n    return 1\n"


def _edit(search: str, replacement: str) -> str:
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replacement}\n>>>>>>> REPLACE\n"


def test_search_must_match_once_and_change_the_file() -> None:
    files = {"app.py": _FILE}

    missing = apply_patch(file_path="app.py", edits=_edit("return 9", "return 2"), files=files)
    repeated = apply_patch(
        file_path="app.py",
        edits=_edit("return 1", "return 2"),
        files={"app.py": "def value():\n    return 1\n    return 1\n"},
    )
    unchanged = apply_patch(file_path="app.py", edits=_edit("return 1", "return 1"), files=files)
    empty = apply_patch(
        file_path="app.py",
        edits="<<<<<<< SEARCH\n\n=======\nreturn 2\n>>>>>>> REPLACE\n",
        files=files,
    )

    assert missing.error is ApplyError.SEARCH_NOT_FOUND
    assert missing.applied_ok is False
    assert repeated.error is ApplyError.SEARCH_AMBIGUOUS
    assert unchanged.error is ApplyError.NO_CHANGE
    assert empty.error is ApplyError.EMPTY_SEARCH
    for result in (missing, repeated, unchanged, empty):
        assert result.new_content is None


def test_crlf_files_match_lf_search_and_keep_their_newlines() -> None:
    files = {"app.py": "def value():\r\n    return 1\r\n"}

    result = apply_patch(
        file_path="app.py", edits=_edit("    return 1", "    return 2"), files=files
    )

    assert result.applied_ok is True
    assert result.new_content == "def value():\r\n    return 2\r\n"
    assert "\n" not in (result.new_content or "").replace("\r\n", "")


def test_paths_must_stay_inside_the_provided_files() -> None:
    files = {
        "app.py": _FILE,
        ".env": "TOKEN=1\n",
        "src/.env.local": "TOKEN=1\n",
        ".git/config": "x\n",
        ".github/workflows/ci.yml": "name: ci\n",
    }
    blocked = (
        "../secrets.py",
        "foo/../../secrets.py",
        "..\\secrets.py",
        "/etc/passwd",
        "C:/secret.txt",
        ".env",
        "src/.env.local",
        ".git/config",
        ".github/workflows/ci.yml",
    )

    for path in blocked:
        result = apply_patch(file_path=path, edits=_edit("return 1", "return 2"), files=files)
        assert result.applied_ok is False, path
        assert result.error in {
            ApplyError.PATH_TRAVERSAL,
            ApplyError.ABSOLUTE_PATH,
            ApplyError.FORBIDDEN_PATH,
        }, path

    missing = apply_patch(file_path="other.py", edits=_edit("return 1", "return 2"), files=files)
    assert missing.error is ApplyError.PATH_NOT_IN_CONTEXT

    allowed = apply_patch(
        file_path=".github/workflows/ci.yml",
        edits=_edit("name: ci", "name: tests"),
        files=files,
        limits=ApplyLimits(allow_workflows=True),
    )
    assert allowed.applied_ok is True
    assert allowed.new_content == "name: tests\n"


def test_a_secret_in_the_replacement_is_rejected() -> None:
    secret = "ghp_" + "a" * 36
    files = {"app.py": _FILE}
    edits = _edit("    return 1", f'    token = "{secret}"')

    result = apply_patch(file_path="app.py", edits=edits, files=files)

    assert result.error is ApplyError.SECRET_IN_REPLACEMENT
    assert result.diff is None
    assert result.new_content is None
    assert secret not in (result.diff or "")


def test_python_syntax_errors_are_rejected_without_raising() -> None:
    result = apply_patch(
        file_path="app.py",
        edits=_edit("    return 1", "    return ("),
        files={"app.py": _FILE},
    )

    assert result.applied_ok is False
    assert result.error is ApplyError.PYTHON_SYNTAX
    assert result.new_content is None
    assert result.diff is not None


def test_edit_and_changed_line_caps() -> None:
    files = {"app.py": "a\nb\nc\n"}
    two_edits = _edit("a", "x") + _edit("b", "y")
    wide = _edit("a\nb\nc", "x\ny\nz")

    too_many = apply_patch(
        file_path="app.py", edits=two_edits, files=files, limits=ApplyLimits(max_edits=1)
    )
    too_wide = apply_patch(
        file_path="app.py",
        edits=wide,
        files=files,
        limits=ApplyLimits(max_changed_lines=1),
    )

    assert too_many.error is ApplyError.TOO_MANY_EDITS
    assert too_wide.error is ApplyError.TOO_MANY_CHANGED_LINES
    assert too_wide.diff is not None


def test_patches_that_weaken_tests_are_rejected() -> None:
    base = "def test_value():\n    assert 1 == 1\n"
    files = {"tests/test_app.py": base}

    skip = apply_patch(
        file_path="tests/test_app.py",
        edits=_edit("def test_value():", "@pytest.mark.skip\ndef test_value():"),
        files=files,
    )
    xfail = apply_patch(
        file_path="tests/test_app.py",
        edits=_edit("def test_value():", "@pytest.mark.xfail\ndef test_value():"),
        files=files,
    )
    call_skip = apply_patch(
        file_path="tests/test_app.py",
        edits=_edit("    assert 1 == 1", "    pytest.skip('nope')"),
        files=files,
    )
    ignored = apply_patch(
        file_path="T.java",
        edits=_edit("void test_one() {", "@Ignore\nvoid test_one() {"),
        files={"T.java": "void test_one() {\n}\n"},
    )
    disabled = apply_patch(
        file_path="T.java",
        edits=_edit("void test_one() {", "@Disabled\nvoid test_one() {"),
        files={"T.java": "void test_one() {\n}\n"},
    )
    deleted = apply_patch(
        file_path="tests/test_app.py",
        edits=_edit("def test_value():\n    assert 1 == 1\n", "def helper():\n    return 1\n"),
        files=files,
    )
    fewer_asserts = apply_patch(
        file_path="tests/test_app.py",
        edits=_edit(
            "    assert 1 == 1\n    assert 2 == 2",
            "    assert 1 == 1",
        ),
        files={"tests/test_app.py": "def test_value():\n    assert 1 == 1\n    assert 2 == 2\n"},
    )
    rewritten = apply_patch(
        file_path="tests/test_app.py",
        edits=_edit("    assert 1 == 1", "    assert 1 == 2"),
        files=files,
    )

    assert skip.error is ApplyError.WEAKENS_TESTS
    assert xfail.error is ApplyError.WEAKENS_TESTS
    assert call_skip.error is ApplyError.WEAKENS_TESTS
    assert ignored.error is ApplyError.WEAKENS_TESTS
    assert disabled.error is ApplyError.WEAKENS_TESTS
    assert deleted.error is ApplyError.WEAKENS_TESTS
    assert fewer_asserts.error is ApplyError.WEAKENS_TESTS
    assert rewritten.applied_ok is True
    assert rewritten.new_content is not None


def test_apply_does_not_raise_for_garbage() -> None:
    result = apply_patch(file_path="app.py", edits="not a patch", files={"app.py": _FILE})

    assert result.error is ApplyError.MALFORMED_EDITS
    assert result.applied_ok is False


@pytest.mark.parametrize("path", [".env", ".github/workflows/ci.yml", "../x"])
def test_forbidden_paths_are_rejected_even_when_listed(path: str) -> None:
    result = apply_patch(
        file_path=path,
        edits=_edit("a", "b"),
        files={path: "a\n", "app.py": "a\n"},
    )

    assert result.applied_ok is False
    assert result.error is not ApplyError.PATH_NOT_IN_CONTEXT
