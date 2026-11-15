"""Secret comparison and the production secret check."""

import pytest
from pydantic import SecretStr

from app.config import Settings, check_required_secrets
from app.main import create_app
from app.security import secrets_equal


@pytest.mark.parametrize(
    "supplied", ["é", "éé", "ééééééééééé", "sha256=é", "tökén-secret", "n8n-secrét"]
)
def test_non_ascii_values_do_not_raise_or_match(supplied: str) -> None:
    assert secrets_equal(supplied, "n8n-secret") is False
    assert secrets_equal(supplied, "tokan-secret") is False


def test_equal_non_ascii_secrets_match() -> None:
    assert secrets_equal("sécret", "sécret") is True


def _prod(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "prod",
        "database_url": "sqlite:///:memory:",
        "github_webhook_secret": SecretStr("hook"),
        "n8n_shared_token": SecretStr("n8n"),
        "api_token": SecretStr("api"),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("env", ["test", "prod"])
@pytest.mark.parametrize("field", ["github_webhook_secret", "n8n_shared_token", "api_token"])
@pytest.mark.parametrize("bad", ["", "replace-me"])
def test_startup_refuses_missing_or_placeholder_secret_outside_dev(
    env: str, field: str, bad: str
) -> None:
    settings = _prod(app_env=env, **{field: SecretStr(bad)})

    with pytest.raises(ValueError, match=field.upper()):
        create_app(settings)
    with pytest.raises(ValueError, match=field.upper()):
        check_required_secrets(settings)


def test_startup_accepts_real_secrets_outside_dev() -> None:
    create_app(_prod())


def test_dev_allows_empty_and_placeholder_secrets() -> None:
    blank = Settings(_env_file=None, app_env="dev", database_url="sqlite:///:memory:")
    placeholder = Settings(
        _env_file=None,
        app_env="dev",
        database_url="sqlite:///:memory:",
        api_token=SecretStr("replace-me"),
    )
    create_app(blank)
    create_app(placeholder)
