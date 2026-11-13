"""The example env file must stay aligned with Settings."""

from pathlib import Path

from app.config import Settings

_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE = _ROOT / ".env.example"


def _example_pairs() -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in _EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def test_env_example_keys_match_settings_fields() -> None:
    """Fail when a Settings field is missing from .env.example, or the file has an extra key."""
    expected = {name.upper() for name in Settings.model_fields}
    assert set(_example_pairs()) == expected


def test_env_example_values_load_into_settings() -> None:
    values = {key.lower(): value for key, value in _example_pairs().items()}
    settings = Settings(_env_file=None, **values)

    assert settings.dry_run is True
    assert settings.app_env == "dev"
    assert settings.classifier_llm_provider == "mock"
    assert settings.github_token.get_secret_value() == "replace-me"
