"""FastAPI dependencies shared by routers."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import session_scope
from app.llm.base import LLMClient
from app.security import secrets_equal


def get_app_settings(request: Request) -> Settings:
    """Return the settings the running app was created with.

    Reading from `app.state` (instead of the global `get_settings()`) lets tests build an app
    with custom settings without touching environment variables.
    """
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Yield a request-scoped session, committing on success and rolling back on error."""
    yield from session_scope(request.app.state.session_factory)


def get_classifier_llm(request: Request) -> LLMClient:
    """Return the classifier client built at startup."""
    return request.app.state.classifier_llm


def get_fixer_llm(request: Request) -> LLMClient:
    """Return the fixer client built at startup."""
    return request.app.state.fixer_llm


def require_api_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Require `Authorization: Bearer API_TOKEN` outside local development.

    Dev with an empty token is open so the dashboard can run locally. Every other
    environment rejects a missing or wrong token. `/health` and the webhooks do not
    use this dependency.

    Raises:
        HTTPException: 401 when the token is required and does not match.
    """
    settings = get_app_settings(request)
    expected = settings.api_token.get_secret_value()
    if settings.app_env == "dev" and not expected:
        return
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not secrets_equal(supplied, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
