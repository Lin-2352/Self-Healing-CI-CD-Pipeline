"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import init_db, make_engine, make_session_factory
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Return test settings that ignore any developer `.env` file."""
    return Settings(_env_file=None, app_env="test", database_url="sqlite:///:memory:")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Yield a test client for an app built with the test settings."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def db_session(settings: Settings) -> Iterator[Session]:
    """Yield a session on a fresh in-memory database with the tables created."""
    engine = make_engine(settings.database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
