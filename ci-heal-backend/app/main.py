"""FastAPI application factory.

Run locally with: `uvicorn app.main:app --reload`
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.api import decisions, failures, health, stats, webhooks
from app.config import (
    Settings,
    check_required_secrets,
    get_settings,
    warn_fix_temperature,
    warn_repeated_classifier_samples,
)
from app.db.session import init_db, make_engine, make_session_factory
from app.llm.factory import build_llm_clients
from app.services.github_client import build_github_client
from app.services.orchestrator import process_failure, recover_stuck

logger = logging.getLogger(__name__)


def _lifespan(engine: Engine, settings: Settings, factory: sessionmaker[Session]):
    """Build the startup/shutdown hook for one engine."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Create tables, reschedule recovered work, and close the pool on shutdown."""
        init_db(engine)
        recovered = recover_stuck(
            factory, settings.processing_stale_seconds, settings.max_recoveries
        )
        _app.state.recovered_ids = list(recovered)
        tasks = [
            asyncio.create_task(
                process_failure(
                    failure_id,
                    settings=settings,
                    session_factory=factory,
                    github=_app.state.github,
                    classifier_llm=_app.state.classifier_llm,
                    fixer_llm=_app.state.fixer_llm,
                )
            )
            for failure_id in recovered
        ]
        _app.state.recovery_tasks = tasks
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            engine.dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI app.

    The engine is created here, but tables are created in the lifespan so that merely
    importing this module does not touch the database file.

    Args:
        settings: Explicit settings (used by tests). Defaults to the environment.

    Returns:
        The ready-to-serve application.
    """
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    check_required_secrets(settings)
    warn_repeated_classifier_samples(settings)
    warn_fix_temperature(settings)
    engine = make_engine(settings.database_url)
    classifier_llm, fixer_llm = build_llm_clients(settings)

    factory = make_session_factory(engine)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=_lifespan(engine, settings, factory),
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.classifier_llm = classifier_llm
    app.state.fixer_llm = fixer_llm
    app.state.github = build_github_client(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(failures.router)
    app.include_router(decisions.router)
    app.include_router(stats.router)

    logger.info(
        "Started %s (env=%s, dry_run=%s)", settings.app_name, settings.app_env, settings.dry_run
    )
    return app


app = create_app()
