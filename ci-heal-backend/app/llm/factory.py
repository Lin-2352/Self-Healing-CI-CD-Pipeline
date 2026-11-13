"""Build the classifier and fixer clients from settings.

An HTTP role with no base URL or no API key raises here, which `create_app` calls during
startup, so a broken deployment fails before it accepts traffic.
"""

from typing import Literal

from pydantic import SecretStr

from app.config import Settings
from app.llm.base import LLMClient
from app.llm.http_client import HttpLLMClient
from app.llm.mock import MockLLMClient

RoleName = Literal["classifier", "fixer"]


def build_llm_clients(settings: Settings) -> tuple[LLMClient, LLMClient]:
    """Return the classifier client and the fixer client described by `settings`."""
    return build_llm_client(settings, "classifier"), build_llm_client(settings, "fixer")


def build_llm_client(settings: Settings, role: RoleName) -> LLMClient:
    """Build one role's client, or raise `ValueError` if its HTTP settings are incomplete."""
    if role == "classifier":
        provider = settings.classifier_llm_provider
        base_url = settings.classifier_llm_base_url
        api_key = settings.classifier_llm_api_key
        model = settings.classifier_llm_model
        timeout = settings.classifier_llm_timeout
        deadline = settings.classifier_llm_deadline
        stream = settings.classifier_llm_stream
        max_retries = settings.classifier_llm_max_retries
    else:
        provider = settings.fixer_llm_provider
        base_url = settings.fixer_llm_base_url
        api_key = settings.fixer_llm_api_key
        model = settings.fixer_llm_model
        timeout = settings.fixer_llm_timeout
        deadline = settings.fixer_llm_deadline
        stream = settings.fixer_llm_stream
        max_retries = settings.fixer_llm_max_retries
    return _build(
        role=role,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        deadline=deadline,
        stream=stream,
        max_retries=max_retries,
    )


def _build(
    *,
    role: RoleName,
    provider: str,
    base_url: str,
    api_key: SecretStr,
    model: str,
    timeout: float,
    deadline: float,
    stream: bool,
    max_retries: int,
) -> LLMClient:
    """Turn one role's fields into a client. The env-var names in the errors match the fields."""
    env_role = role.upper()
    if provider == "mock":
        return MockLLMClient()
    if provider != "http":
        raise ValueError(f"{env_role}_LLM_PROVIDER must be 'mock' or 'http'")
    if not base_url.strip():
        raise ValueError(f"{env_role} LLM provider is http but {env_role}_LLM_BASE_URL is empty")
    if not api_key.get_secret_value().strip():
        raise ValueError(f"{env_role} LLM provider is http but {env_role}_LLM_API_KEY is empty")
    return HttpLLMClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        deadline=deadline,
        stream=stream,
        max_retries=max_retries,
    )
