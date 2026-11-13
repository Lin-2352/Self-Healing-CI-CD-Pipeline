"""The replay script stays offline and does not leave a database behind."""

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _replay(fixture: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["DRY_RUN"] = "false"
    env["CLASSIFIER_LLM_PROVIDER"] = "http"
    env["FIXER_LLM_PROVIDER"] = "http"
    return subprocess.run(
        [sys.executable, "scripts/replay_failure.py", fixture],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_replay_dependency_and_flaky_are_dry_run_and_offline(tmp_path: Path) -> None:
    database = tmp_path / "must-not-be-created.db"
    dependency = _replay("fixtures/dependency.log", database)
    flaky = _replay("fixtures/flaky.log", database)

    assert dependency.returncode == 0, dependency.stderr
    assert "dry_run: True" in dependency.stdout
    assert "database: sqlite:///:memory: (discarded)" in dependency.stdout
    assert "redaction_count: 0" in dependency.stdout
    assert "stage1: action=candidate_fix" in dependency.stdout
    assert "agreement: 1.000" in dependency.stdout
    assert "final: action=fix" in dependency.stdout
    assert "outcome=pending" in dependency.stdout
    assert "verified_success" not in dependency.stdout
    assert flaky.returncode == 0, flaky.stderr
    assert "stage1: action=retry" in flaky.stdout
    assert "final: action=retry" in flaky.stdout
    assert "agreement: not scored" in flaky.stdout
    assert database.exists() is False


def test_replay_secrets_fixture_counts_redactions(tmp_path: Path) -> None:
    result = _replay("fixtures/secrets.log", tmp_path / "absent.db")

    assert result.returncode == 0, result.stderr
    assert "redaction_count: 0" not in result.stdout
    assert "ghp_" not in result.stdout
    assert "AKIA" not in result.stdout


def test_replay_prompt_injection_keeps_the_labeled_category(tmp_path: Path) -> None:
    result = _replay("fixtures/prompt_injection.log", tmp_path / "absent.db")

    assert result.returncode == 0, result.stderr
    assert "classification: test_failure" in result.stdout
    assert "sample: index=0 category=test_failure" in result.stdout
