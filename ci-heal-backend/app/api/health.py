"""Liveness and readiness endpoints."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.config import Settings
from app.deps import get_app_settings, get_classifier_llm, get_db, get_fixer_llm
from app.llm.base import LLMClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Body returned by `GET /health`."""

    status: Literal["ok"]
    app: str
    version: str
    environment: str
    dry_run: bool


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(get_app_settings)]) -> HealthResponse:
    """Report that the process is up, plus the flags an operator most needs to see."""
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        dry_run=settings.dry_run,
    )


class ReadyResponse(BaseModel):
    """Body returned by `GET /health/ready`. Status follows the database only."""

    status: Literal["ok", "unavailable"]
    database: Literal["ok", "error"]


class LLMHealthResponse(BaseModel):
    """Body returned by `GET /health/llm`. Informational: the status code is always 200."""

    classifier_llm: Literal["ok", "error"]
    fixer_llm: Literal["ok", "error"]


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
def ready(session: Annotated[Session, Depends(get_db)]) -> ReadyResponse | JSONResponse:
    """Check the database.

    The status code is 503 only when the database check fails, so a platform health check
    can take the instance out of rotation. A model outage is reported on `GET /health/llm`
    and does not change this status. Exception text is not returned: a database error can
    contain the connection string.
    """
    database = _check_database(session)
    body = ReadyResponse(status="ok" if database == "ok" else "unavailable", database=database)
    if database != "ok":
        return JSONResponse(status_code=503, content=body.model_dump())
    return body


@router.get("/health/llm", response_model=LLMHealthResponse)
async def llm_health(
    classifier_llm: Annotated[LLMClient, Depends(get_classifier_llm)],
    fixer_llm: Annotated[LLMClient, Depends(get_fixer_llm)],
) -> LLMHealthResponse:
    """Report whether each model provider answers. Always HTTP 200.

    An outage here must not take the service out of rotation. Callers that care about the
    models read the fields; they do not read the status code.
    """
    return LLMHealthResponse(
        classifier_llm=await _check_llm(classifier_llm, "classifier"),
        fixer_llm=await _check_llm(fixer_llm, "fixer"),
    )


def _check_database(session: Session) -> Literal["ok", "error"]:
    """Return `ok` when the database answers `SELECT 1`."""
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("database readiness check failed: %s", type(exc).__name__)
        return "error"
    return "ok"


async def _check_llm(client: LLMClient, role: str) -> Literal["ok", "error"]:
    """Return `ok` when the provider's readiness check succeeds."""
    try:
        reachable = await client.ping()
    except Exception as exc:
        logger.warning("%s LLM readiness check failed: %s", role, type(exc).__name__)
        return "error"
    if not reachable:
        logger.warning("%s LLM readiness check failed", role)
        return "error"
    return "ok"
