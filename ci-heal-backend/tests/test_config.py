"""Tests for environment-driven settings."""

import logging

import pytest

from app.config import Settings
from app.main import create_app


def test_defaults_are_safe() -> None:
    """With no environment, dry-run is on and SQLite is the database."""
    settings = Settings(_env_file=None)

    assert settings.dry_run is True
    assert settings.fix_max_changed_lines == 50
    assert settings.database_url.startswith("sqlite")


def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables (case-insensitive) override every default."""
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/ci")
    monkeypatch.setenv("APP_ENV", "prod")

    settings = Settings(_env_file=None)

    assert settings.dry_run is False
    assert settings.database_url == "postgresql+psycopg://u:p@db/ci"
    assert settings.app_env == "prod"


def test_cors_origin_list_splits_and_trims() -> None:
    """Comma-separated origins become a list without blanks or stray spaces."""
    settings = Settings(_env_file=None, cors_origins=" http://a.test , ,http://b.test")

    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_invalid_env_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value outside the allowed set fails fast at startup instead of misbehaving later."""
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_api_keys_stay_out_of_repr() -> None:
    settings = Settings(_env_file=None, classifier_llm_api_key="super-secret-key")

    assert "super-secret-key" not in repr(settings)
    assert "super-secret-key" not in repr(settings.classifier_llm_api_key)
    assert settings.classifier_llm_provider == "mock"


def test_default_thresholds_keep_the_classify_gate_at_075() -> None:
    settings = Settings(_env_file=None)

    thresholds = settings.to_thresholds()

    assert thresholds.classify == 0.75
    assert thresholds.agreement == 0.6
    assert thresholds.flaky == 0.75
    assert thresholds.max_retries == 1
    assert thresholds.max_heal_attempts == 3
    assert dict(thresholds.by_category) == {}


def test_category_threshold_overrides_must_name_real_categories() -> None:
    settings = Settings(_env_file=None, category_thresholds='{"test_failure": 0.9, "unknown": 0}')

    assert settings.to_thresholds().by_category["test_failure"] == 0.9

    for raw in (
        '{"not_a_category": 0.5}',
        '{"test_failure": 1.2}',
        '{"test_failure": true}',
        "[0.5]",
        "{",
    ):
        with pytest.raises(ValueError, match="CATEGORY_THRESHOLDS"):
            Settings(_env_file=None, category_thresholds=raw)


def test_startup_warns_when_extra_samples_cannot_differ(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite:///:memory:",
        classifier_samples=3,
        classifier_temperature=0,
    )

    with caplog.at_level(logging.WARNING):
        create_app(settings)

    assert "CLASSIFIER_TEMPERATURE" in caplog.text


def test_startup_is_quiet_when_samples_can_differ(caplog: pytest.LogCaptureFixture) -> None:
    quiet = (
        Settings(_env_file=None, app_env="test", database_url="sqlite:///:memory:"),
        Settings(
            _env_file=None,
            app_env="test",
            database_url="sqlite:///:memory:",
            classifier_samples=3,
            classifier_temperature=0.7,
        ),
    )

    for settings in quiet:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            create_app(settings)
        assert "CLASSIFIER_TEMPERATURE" not in caplog.text


def test_startup_warns_when_fix_temperature_is_zero(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite:///:memory:",
        fix_temperature=0,
    )

    with caplog.at_level(logging.WARNING):
        create_app(settings)

    assert "FIX_TEMPERATURE" in caplog.text
