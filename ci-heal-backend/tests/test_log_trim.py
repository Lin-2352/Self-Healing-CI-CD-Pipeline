"""The log trimmer keeps the same region as the n8n trimCiLog."""

from app.core.log_trim import LOG_MAX_CHARS, LOG_MAX_LINES, LOG_UNAVAILABLE, trim_ci_log


def test_empty_and_errorless_logs_are_unavailable() -> None:
    assert trim_ci_log("") == LOG_UNAVAILABLE
    assert trim_ci_log("   \n") == LOG_UNAVAILABLE
    assert trim_ci_log("npm test\npassed nothing, but no error marker") == LOG_UNAVAILABLE


def test_trim_keeps_the_region_between_endgroup_and_error() -> None:
    log = "\n".join(
        [
            "2026-01-01T00:00:00.0000000Z ##[group]Run tests",
            "2026-01-01T00:00:01.0000000Z echo setup",
            "2026-01-01T00:00:02.0000000Z ##[endgroup]",
            "2026-01-01T00:00:03.0000000Z \x1b[31mFAIL\x1b[0m /home/runner/work/demo/demo/test.js",
            "2026-01-01T00:00:04.0000000Z ##[error]Process completed with exit code 1.",
            "2026-01-01T00:00:05.0000000Z Post job cleanup.",
        ]
    )

    assert trim_ci_log(log) == "FAIL test.js\nProcess completed with exit code 1."


def test_trim_caps_lines_and_characters_from_the_end() -> None:
    body = [f"line {index}" for index in range(LOG_MAX_LINES + 10)]
    log = "\n".join(body + ["##[error]boom"])

    trimmed = trim_ci_log(log)

    assert trimmed.startswith("line 11")
    assert trimmed.endswith("boom")
    assert trimmed.count("\n") == LOG_MAX_LINES - 1

    huge = "x" * (LOG_MAX_CHARS + 50) + "\n##[error]tail"
    assert len(trim_ci_log(huge)) == LOG_MAX_CHARS
    assert trim_ci_log(huge).endswith("tail")
