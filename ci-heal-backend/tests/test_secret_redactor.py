"""Secrets are removed before a log is trimmed or stored."""

import pytest

from app.core.log_trim import LOG_MAX_CHARS, LOG_UNAVAILABLE
from app.core.redacted import RedactedText
from app.services.secret_redactor import prepare_context, prepare_log, redact, redact_outbound

_GHP = "ghp_" + "a" * 36
_GHO = "gho_" + "b" * 36
_GHS = "ghs_" + "c" * 36
_FINE = "github_pat_" + "d" * 22 + "_" + "e" * 59
_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
_GOOGLE = "AIza" + "A" * 35
_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJBALRiMLAHudeSA==\n-----END RSA PRIVATE KEY-----"


def test_known_secret_formats_are_replaced_and_labelled() -> None:
    text = "\n".join(
        [
            f"token {_GHP} {_GHO} {_GHS} {_FINE}",
            "key AKIAIOSFODNN7EXAMPLE and ASIAABCDEFGHIJKLMNOP",
            "Authorization: Bearer ya29.superSecretValue",
            f"jwt {_JWT}",
            "slack xoxb-123456789012-abcdefghij",
            "stripe sk_live_" + "b" * 24,
            f"google {_GOOGLE}",
            "clone https://deployer:s3cret@github.com/acme/demo.git",
            "DB_PASSWORD=hunter2 API_KEY: 'abcd1234'",
            "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            _PEM,
        ]
    )

    result = redact(text)

    assert result.count == 16
    for secret in (_GHP, _GHO, _GHS, _FINE, _JWT, "AKIAIOSFODNN7EXAMPLE", "hunter2", "s3cret"):
        assert secret not in result.text
    assert "BEGIN RSA PRIVATE KEY" not in result.text
    assert "[REDACTED:GITHUB_TOKEN]" in result.text
    assert "[REDACTED:AWS_ACCESS_KEY]" in result.text
    assert "[REDACTED:BEARER]" in result.text
    assert "[REDACTED:JWT]" in result.text
    assert "[REDACTED:SLACK_TOKEN]" in result.text
    assert "[REDACTED:STRIPE_KEY]" in result.text
    assert "[REDACTED:GOOGLE_API_KEY]" in result.text
    assert "https://[REDACTED:URL_CREDENTIALS]@github.com/acme/demo.git" in result.text
    assert "DB_PASSWORD=[REDACTED:ENV_SECRET]" in result.text
    assert "aws_secret_access_key=[REDACTED:ENV_SECRET]" in result.text
    assert "[REDACTED:PRIVATE_KEY]" in result.text


def test_redaction_is_idempotent_and_leaves_ordinary_logs_alone() -> None:
    ordinary = "\n".join(
        [
            "commit abc1234def5678901234567890abcdef12345678",
            "PASSWORD=null TOKEN=none API_KEY=",
            "##[error]Process completed with exit code 1.",
        ]
    )
    assert redact(ordinary).count == 0
    assert redact(ordinary).text == ordinary
    assert redact(None).count == 0

    redacted = redact(f"TOKEN={_GHP}")
    again = redact(redacted.text)
    assert redacted.text == "TOKEN=[REDACTED:GITHUB_TOKEN]"
    assert again.text == redacted.text
    assert again.count == 0


def test_prepare_log_redacts_the_full_text_then_trims() -> None:
    outside = "ghp_" + "f" * 36
    inside = "ghs_" + "e" * 36
    log = "\n".join(
        [
            f"2026-01-01T00:00:00.0000000Z echo TOKEN={outside}",
            "2026-01-01T00:00:01.0000000Z ##[endgroup]",
            f"2026-01-01T00:00:02.0000000Z leaked {inside}",
            "2026-01-01T00:00:03.0000000Z ##[error]Process completed with exit code 1.",
        ]
    )

    excerpt, count = prepare_log(log)

    assert count == 2
    assert outside not in excerpt
    assert inside not in excerpt
    assert excerpt == "leaked [REDACTED:GITHUB_TOKEN]\nProcess completed with exit code 1."
    assert isinstance(excerpt, RedactedText)
    assert prepare_log("nothing useful") == (LOG_UNAVAILABLE, 0)


def test_redacted_text_cannot_be_built_by_hand() -> None:
    with pytest.raises(TypeError):
        RedactedText("ghp_" + "a" * 36)


def test_trusted_text_can_be_joined_and_formatted() -> None:
    static = RedactedText.trusted("Classify this log:\n")
    evidence = redact("token=ghp_" + "a" * 36).text

    combined = RedactedText.join([static, evidence])
    filled = RedactedText.format("prefix {body} suffix", body=evidence)

    assert combined.startswith("Classify this log:\n")
    assert "ghp_" not in combined
    assert filled.startswith("prefix ")
    assert isinstance(combined, RedactedText)
    assert isinstance(filled, RedactedText)
    with pytest.raises(TypeError):
        RedactedText.join([static, "still a secret"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        RedactedText.format("{body}", body="still a secret")  # type: ignore[arg-type]


def test_assignments_flags_and_provider_tokens_are_redacted() -> None:
    azure = (
        "DefaultEndpointsProtocol=https;AccountName=devstore;"
        "AccountKey=abcdEFGH0123456789+/=;"
        "EndpointSuffix=core.windows.net"
    )
    text = "\n".join(
        [
            '{"password": "hello world", "api_key": "plain"}',
            "credentials: 'keep me secret'",
            "DB_PASSWD=hunter2 PWD=s3cret private_key: abcdef access_key=notreal",
            "--token abc --password=s3cret --api-key 'with spaces'",
            "Authorization: Basic dXNlcjpwYXNz",
            "npm_" + "a" * 36,
            "pypi-" + "b" * 20,
            "dckr_pat_" + "c" * 24,
            "SG." + "d" * 22 + "." + "e" * 43,
            azure,
        ]
    )

    result = redact(text)

    for secret in ("hello world", "hunter2", "dXNlcjpwYXNz", "abcdEFGH0123456789+/=", "npm_"):
        assert secret not in result.text
    assert "[REDACTED:ENV_SECRET]" in result.text
    assert "--token [REDACTED:CLI_SECRET]" in result.text
    assert "--password=[REDACTED:CLI_SECRET]" in result.text
    assert "[REDACTED:BASIC_AUTH]" in result.text
    assert "[REDACTED:NPM_TOKEN]" in result.text
    assert "[REDACTED:PYPI_TOKEN]" in result.text
    assert "[REDACTED:DOCKER_TOKEN]" in result.text
    assert "[REDACTED:SENDGRID_KEY]" in result.text
    assert "[REDACTED:AZURE_STORAGE_KEY]" in result.text
    assert result.count == 16


def test_diffs_files_and_model_output_are_redacted_too() -> None:
    secret = "ghp_" + "d" * 36
    context = prepare_context(
        log="##[error]boom",
        diff=f"TOKEN={secret}",
        files={"app.py": f"password = '{secret}'"},
    )
    outbound = redact_outbound(f"Fixes the login. Token was {secret}.")

    assert isinstance(context.diff, RedactedText)
    assert isinstance(context.files["app.py"], RedactedText)
    assert secret not in context.diff
    assert secret not in context.files["app.py"]
    assert secret not in outbound.text
    assert context.redaction_count == 2
    assert outbound.count == 1


def test_edges_crlf_long_lines_and_unicode() -> None:
    start = "ghp_" + "e" * 36
    end = "ghs_" + "f" * 36
    excerpt, count = prepare_log(f"{start}\r\n##[error]boom {end}")

    assert count == 2
    assert start not in excerpt
    assert end not in excerpt
    assert excerpt.startswith("[REDACTED:GITHUB_TOKEN]")
    assert excerpt.endswith("boom [REDACTED:GITHUB_TOKEN]")

    buried = "ghp_" + "c" * 36
    filler = "字" * (LOG_MAX_CHARS + 50)
    long_excerpt, long_count = prepare_log(f"{buried} {filler}\n##[error]tail")
    assert long_count == 1
    assert buried not in long_excerpt
    assert long_excerpt.endswith("tail")
    assert "字" in long_excerpt

    mixed = redact(f"构建失败 {_GHP} café")
    assert "构建失败" in mixed.text
    assert "café" in mixed.text
    assert _GHP not in mixed.text


def test_ordinary_identifiers_are_not_redacted() -> None:
    sha = "a" * 40
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    publishable = "pk_live_" + "c" * 24
    text = "\n".join(
        [
            sha,
            uuid,
            "TOKEN=***",
            "Authorization: Basic ***",
            "Authorization: Bearer ***",
            publishable,
            "tokenization: 1",
            "##[error]Process completed with exit code 1.",
        ]
    )

    result = redact(text)

    assert result.count == 0
    assert result.text == text
