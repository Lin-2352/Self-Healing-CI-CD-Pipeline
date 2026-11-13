"""Application settings, loaded only from environment variables (and an optional `.env`).

Every tunable lives here so that switching LLM providers, thresholds, or databases is an
env change, never a code change. API keys are `SecretStr`: they stay out of logs and `repr`.
The decision engine does not read this module. Callers build a `Thresholds` value and pass it in.
"""

import json
import logging
import math
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.decision_engine import Thresholds
from app.core.enums import FailureCategory

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Typed view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ci-heal-backend"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Safe by default: nothing is written to GitHub unless DRY_RUN=false is set explicitly.
    dry_run: bool = True
    github_token: SecretStr = SecretStr("")
    github_api_url: str = "https://api.github.com"
    github_timeout: float = Field(default=30, gt=0, le=300)
    github_max_retries: int = Field(default=2, ge=0, le=5)
    github_max_file_bytes: int = Field(default=100_000, ge=1, le=5_000_000)
    github_max_log_bytes: int = Field(default=1_000_000, ge=1, le=20_000_000)
    # Ref writes to this name are refused, along with main and master.
    github_default_branch: str = Field(default="main", min_length=1, max_length=255)
    github_webhook_secret: SecretStr = SecretStr("")
    # Login of the token owner. Runs by this actor are ignored so a fix cannot retrigger itself.
    github_bot_login: str = ""
    n8n_shared_token: SecretStr = SecretStr("")
    # Dashboard and operator API. Outside dev, requests without this token are rejected.
    api_token: SecretStr = SecretStr("")
    # Comma-separated owner/name list. An empty list allows no repository.
    allowed_repos: str = ""
    webhook_max_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    # Whole pipeline for one failure, including model calls and GitHub reads.
    pipeline_timeout_seconds: float = Field(default=180, gt=0, le=1800)
    # A failure left in `processing` longer than this is requeued on startup.
    processing_stale_seconds: int = Field(default=900, ge=30, le=86_400)
    # Startup reschedules each queued or stale failure this many times, then marks it error.
    max_recoveries: int = Field(default=3, ge=0, le=20)

    database_url: str = "sqlite:///./ci_heal.db"

    # Decision gates. 0.75 is intentional: a 2/3 self-consistency vote is about 0.667 and
    # escalates. Calibration stats are what should move this, not a one-off failure.
    classify_threshold: float = Field(default=0.75, ge=0, le=1)
    agreement_threshold: float = Field(default=0.6, ge=0, le=1)
    flaky_threshold: float = Field(default=0.75, ge=0, le=1)
    # Flaky re-runs already requested. Distinct from CLASSIFIER_MAX_RETRIES (parse re-asks).
    max_retries: int = Field(default=1, ge=0, le=10)
    max_heal_attempts: int = Field(default=3, ge=1, le=20)
    # JSON object of category -> confidence gate in [0, 1]. Overrides the gates above.
    category_thresholds: str = "{}"

    # Comma-separated origins allowed to call the API from a browser (the React dashboard).
    cors_origins: str = Field(default="http://localhost:5173,http://localhost:3000")

    # Two roles so classification can use a small hosted model while fixing uses Kaggle.
    # `mock` is the default: the app boots and the tests run with no network and no key.
    classifier_llm_provider: Literal["mock", "http"] = "mock"
    classifier_llm_base_url: str = ""
    classifier_llm_api_key: SecretStr = SecretStr("")
    classifier_llm_model: str = "local"
    classifier_llm_timeout: float = Field(default=60, gt=0, le=600)
    # Whole classify call, including HTTP retries and the waits between them.
    classifier_llm_deadline: float = Field(default=180, gt=0, le=1800)
    classifier_llm_stream: bool = True
    classifier_llm_max_retries: int = Field(default=2, ge=0, le=5)

    # Self-consistency. Default is one sample so a normal run stays cheap. Set samples to 3
    # and raise the temperature for an eval or a demo, or the three samples will repeat.
    classifier_samples: int = Field(default=1, ge=1, le=5)
    classifier_temperature: float = Field(default=0.0, ge=0, le=2)
    classifier_max_retries: int = Field(default=2, ge=0, le=5)
    classifier_max_tokens: int = Field(default=512, ge=16, le=4096)

    fixer_llm_provider: Literal["mock", "http"] = "mock"
    fixer_llm_base_url: str = ""
    fixer_llm_api_key: SecretStr = SecretStr("")
    fixer_llm_model: str = "local"
    fixer_llm_timeout: float = Field(default=120, gt=0, le=600)
    fixer_llm_deadline: float = Field(default=360, gt=0, le=1800)
    fixer_llm_stream: bool = True
    fixer_llm_max_retries: int = Field(default=2, ge=0, le=5)

    # Fix candidates. Temperature 0 makes every candidate identical, so agreement cannot move.
    fix_candidates: int = Field(default=3, ge=1, le=5)
    fix_temperature: float = Field(default=0.7, ge=0, le=2)
    fix_max_tokens: int = Field(default=4096, ge=16, le=8192)
    fix_max_edits: int = Field(default=5, ge=1, le=20)
    fix_max_changed_lines: int = Field(default=50, ge=1, le=2000)
    # .github/workflows stays blocked unless this is set. The other forbidden paths do not.
    fix_allow_workflows: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        """Return `cors_origins` split into a clean list, ignoring blanks."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_repo_set(self) -> frozenset[str]:
        """Return the repositories webhooks may record. Empty means none."""
        return frozenset(item.strip() for item in self.allowed_repos.split(",") if item.strip())

    @field_validator("category_thresholds")
    @classmethod
    def _check_category_thresholds(cls, raw: str) -> str:
        """Reject a category map that names an unknown label or a value outside [0, 1]."""
        parse_category_thresholds(raw)
        return raw

    def to_thresholds(self) -> Thresholds:
        """Copy the decision gates into the frozen object the engine accepts.

        Returns:
            Thresholds for one `decide` call. The engine never reads the environment itself.
        """
        return Thresholds(
            classify=self.classify_threshold,
            agreement=self.agreement_threshold,
            flaky=self.flaky_threshold,
            max_retries=self.max_retries,
            max_heal_attempts=self.max_heal_attempts,
            by_category=parse_category_thresholds(self.category_thresholds),
        )


def parse_category_thresholds(raw: str) -> dict[str, float]:
    """Parse `CATEGORY_THRESHOLDS`.

    Keys must be `FailureCategory` values. Values must be numbers in [0, 1]. Bool is
    rejected because JSON `true` would otherwise become the gate `1`.

    Args:
        raw: The environment string. `{}` means no overrides.

    Returns:
        Category label to confidence gate.

    Raises:
        ValueError: The string is not a JSON object, a key is not a category, or a value
            is outside [0, 1].
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("CATEGORY_THRESHOLDS must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError("CATEGORY_THRESHOLDS must be a JSON object")
    allowed = {item.value for item in FailureCategory}
    cleaned: dict[str, float] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or key not in allowed:
            names = ", ".join(sorted(allowed))
            raise ValueError(
                f"CATEGORY_THRESHOLDS key {key!r} is not a category. Expected one of: {names}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"CATEGORY_THRESHOLDS value for {key} must be a number from 0 to 1")
        if math.isnan(float(value)) or math.isinf(float(value)) or not 0 <= float(value) <= 1:
            raise ValueError(f"CATEGORY_THRESHOLDS value for {key} must be a number from 0 to 1")
        cleaned[key] = float(value)
    return cleaned


def warn_repeated_classifier_samples(settings: Settings) -> None:
    """Warn when extra classifier samples would all be the same reply.

    Temperature 0 makes every sample identical, so a majority vote cannot move.
    Startup continues. Set `CLASSIFIER_TEMPERATURE` above 0 when `CLASSIFIER_SAMPLES` is
    greater than 1.

    Args:
        settings: The settings the process is starting with.
    """
    if settings.classifier_samples > 1 and settings.classifier_temperature == 0:
        logger.warning(
            "CLASSIFIER_SAMPLES is %s and CLASSIFIER_TEMPERATURE is 0, so every sample "
            "will match. Raise CLASSIFIER_TEMPERATURE if those samples should differ.",
            settings.classifier_samples,
        )


def warn_fix_temperature(settings: Settings) -> None:
    """Warn when fix candidates would all be the same patch.

    Temperature 0 repeats one completion, so the agreement score cannot measure diversity.
    Startup continues. Set `FIX_TEMPERATURE` above 0.

    Args:
        settings: The settings the process is starting with.
    """
    if settings.fix_temperature == 0:
        logger.warning(
            "FIX_TEMPERATURE is 0, so every fix candidate will match. "
            "Raise FIX_TEMPERATURE so agreement can tell the candidates apart."
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, built once on first use."""
    return Settings()
