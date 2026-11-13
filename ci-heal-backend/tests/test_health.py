"""Tests for the health endpoints."""

from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings
from app.deps import get_db
from app.main import create_app


def test_health_reports_ok_and_flags(client: TestClient) -> None:
    """`/health` returns 200 with version, environment and the dry-run flag."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "ci-heal-backend",
        "version": __version__,
        "environment": "test",
        "dry_run": True,
    }


def test_cors_allows_dashboard_origin(client: TestClient) -> None:
    """A browser preflight from the default dashboard origin is allowed."""
    response = client.options(
        "/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_ready_reports_the_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_ready_returns_503_when_the_database_check_fails(settings: Settings) -> None:
    app = create_app(settings)

    def broken_db():
        class _Broken:
            def execute(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("password=super-secret")

        yield _Broken()

    app.dependency_overrides[get_db] = broken_db
    with TestClient(app) as test_client:
        response = test_client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "error"}
    assert "super-secret" not in response.text


def test_a_down_model_does_not_make_the_service_unready(settings: Settings) -> None:
    app = create_app(settings)

    class _Down:
        async def ping(self) -> bool:
            return False

    app.state.classifier_llm = _Down()
    with TestClient(app) as test_client:
        ready = test_client.get("/health/ready")
        models = test_client.get("/health/llm")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": "ok"}
    assert models.status_code == 200
    assert models.json() == {"classifier_llm": "error", "fixer_llm": "ok"}
