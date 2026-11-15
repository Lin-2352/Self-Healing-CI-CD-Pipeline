"""Stats aggregates against a seeded database. Expected values are computed by hand."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.core.enums import DecisionAction, DecisionOutcome, FailureCategory, FailureSource
from app.db.repositories import ClassificationRepository, DecisionRepository, FailureRepository
from app.main import create_app
from app.schemas.classification import ClassificationCreate
from app.schemas.decision import DecisionCreate
from app.schemas.failure import FailureCreate

_API = {"Authorization": "Bearer api-secret"}
_SHA = "abc1234def5678"
_DAY1 = datetime(2024, 1, 1, tzinfo=UTC)
_DAY2 = datetime(2024, 1, 2, tzinfo=UTC)
_DAY3 = datetime(2024, 1, 3, tzinfo=UTC)
_LATER = datetime(2030, 1, 1, tzinfo=UTC)
# bins=4. Classifier ECE = (2*0.5 + 2*0.375) / 6 = 7/24.
# Agreement uses the four FIX rows only: (2*0.5 + 2*0.375) / 4 = 0.4375.
_CLASSIFIER_ECE = 7 / 24
_AGREEMENT_ECE = 0.4375


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite:///:memory:",
        api_token=SecretStr("api-secret"),
        github_webhook_secret=SecretStr("hook"),
        n8n_shared_token=SecretStr("n8n"),
    )


def _seed(session: object) -> None:
    """Insert the rows the hand calculation below depends on."""
    failures = FailureRepository(session)  # type: ignore[arg-type]
    decisions = DecisionRepository(session)  # type: ignore[arg-type]
    classifications = ClassificationRepository(session)  # type: ignore[arg-type]
    run = 0

    def add(
        *,
        action: DecisionAction,
        outcome: DecisionOutcome,
        category: FailureCategory,
        confidence: float,
        agreement: float | None,
        when: datetime,
        repo: str = "acme/demo",
        dry_run: bool = False,
        ground_truth: FailureCategory | None = None,
        predicted: FailureCategory | None = None,
    ) -> None:
        nonlocal run
        run += 1
        failure, _created = failures.get_or_create(
            FailureCreate(
                repo=repo,
                run_id=run,
                run_attempt=1,
                branch="main",
                commit_sha=_SHA,
                source=FailureSource.GITHUB,
            )
        )
        failure.created_at = when
        if ground_truth is not None:
            failure.ground_truth_category = ground_truth
        row = decisions.add(
            DecisionCreate(
                failure_id=failure.id,
                action=action,
                category=category,
                classifier_confidence=confidence,
                agreement_score=agreement,
                reason="seed",
                dry_run=dry_run,
                repo=repo,
                run_id=run,
                outcome=outcome,
                fix_branch="ai-fix-seed" if action is DecisionAction.FIX else None,
                expected_run_attempt=2 if action is DecisionAction.RETRY else None,
            )
        )
        row.created_at = when
        if predicted is not None:
            classifications.add(
                ClassificationCreate(
                    failure_id=failure.id,
                    category=predicted,
                    confidence=confidence,
                    reasoning="seed",
                    model="mock",
                    raw_output="{}",
                )
            )

    # Six resolved rows. Bins of width 0.25: 0, empty, 0.5, and the last bin (0.75 and 1.0).
    add(
        action=DecisionAction.FIX,
        outcome=DecisionOutcome.VERIFIED_FAILURE,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=0.0,
        agreement=0.0,
        when=_DAY1,
    )
    add(
        action=DecisionAction.FIX,
        outcome=DecisionOutcome.VERIFIED_SUCCESS,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=0.0,
        agreement=0.0,
        when=_DAY1,
    )
    add(
        action=DecisionAction.RETRY,
        outcome=DecisionOutcome.RETRY_SUCCESS,
        category=FailureCategory.FLAKY_OR_NETWORK,
        confidence=0.5,
        agreement=None,
        when=_DAY2,
    )
    add(
        action=DecisionAction.RETRY,
        outcome=DecisionOutcome.RETRY_FAILURE,
        category=FailureCategory.FLAKY_OR_NETWORK,
        confidence=0.5,
        agreement=None,
        when=_DAY2,
    )
    add(
        action=DecisionAction.FIX,
        outcome=DecisionOutcome.VERIFIED_SUCCESS,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=1.0,
        agreement=1.0,
        when=_DAY3,
    )
    add(
        action=DecisionAction.FIX,
        outcome=DecisionOutcome.VERIFIED_FAILURE,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=0.75,
        agreement=0.75,
        when=_DAY3,
    )
    # Excluded from the hand window: too late, another repo, dry-run, still pending.
    add(
        action=DecisionAction.RETRY,
        outcome=DecisionOutcome.RETRY_FAILURE,
        category=FailureCategory.FLAKY_OR_NETWORK,
        confidence=0.5,
        agreement=None,
        when=_LATER,
    )
    add(
        action=DecisionAction.FIX,
        outcome=DecisionOutcome.VERIFIED_SUCCESS,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=0.0,
        agreement=0.0,
        when=_DAY1,
        repo="other/demo",
    )
    add(
        action=DecisionAction.FIX,
        outcome=DecisionOutcome.VERIFIED_SUCCESS,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=0.0,
        agreement=0.0,
        when=_DAY1,
        dry_run=True,
        ground_truth=FailureCategory.DEPENDENCY_ERROR,
        predicted=FailureCategory.DEPENDENCY_ERROR,
    )
    add(
        action=DecisionAction.FIX,
        outcome=DecisionOutcome.PENDING,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=0.9,
        agreement=0.9,
        when=_DAY1,
    )
    # Labeled rows. Their decisions stay pending, so they do not change the decision ECE.
    add(
        action=DecisionAction.ESCALATE,
        outcome=DecisionOutcome.PENDING,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=1.0,
        agreement=None,
        when=_DAY1,
        ground_truth=FailureCategory.DEPENDENCY_ERROR,
        predicted=FailureCategory.DEPENDENCY_ERROR,
    )
    add(
        action=DecisionAction.ESCALATE,
        outcome=DecisionOutcome.PENDING,
        category=FailureCategory.DEPENDENCY_ERROR,
        confidence=0.0,
        agreement=None,
        when=_DAY1,
        ground_truth=FailureCategory.TEST_FAILURE,
        predicted=FailureCategory.DEPENDENCY_ERROR,
    )
    add(
        action=DecisionAction.ESCALATE,
        outcome=DecisionOutcome.PENDING,
        category=FailureCategory.TEST_FAILURE,
        confidence=0.5,
        agreement=None,
        when=_DAY1,
        ground_truth=FailureCategory.TEST_FAILURE,
        predicted=FailureCategory.TEST_FAILURE,
    )


def _client() -> TestClient:
    return TestClient(create_app(_settings()))


def _window() -> str:
    return "repo=acme/demo&until=2025-01-01T00:00:00Z"


def _bin(series: dict[str, object], lower: float) -> dict[str, object]:
    bins = series["bins"]
    assert isinstance(bins, list)
    found = [item for item in bins if item["lower"] == lower]
    assert len(found) == 1
    return found[0]


def test_seeded_stats_match_the_hand_calculation() -> None:
    with _client() as client:
        session = client.app.state.session_factory()
        _seed(session)
        session.commit()
        session.close()
        summary = client.get(f"/stats/summary?{_window()}", headers=_API)
        calibration = client.get(f"/stats/calibration?bins=4&{_window()}", headers=_API)
        by_category = client.get(f"/stats/by-category?{_window()}", headers=_API)
        by_action = client.get(f"/stats/by-action?{_window()}", headers=_API)
        by_outcome = client.get(f"/stats/by-outcome?{_window()}", headers=_API)
        timeline = client.get(f"/stats/timeline?{_window()}", headers=_API)

    assert summary.status_code == 200
    body = summary.json()
    assert body["n_total"] == 6
    assert body["successes"] == 3
    assert body["success_rate"] == pytest.approx(0.5)
    assert body["n_labeled"] == 3
    assert body["accuracy"] == pytest.approx(2 / 3)

    report = calibration.json()
    classifier = report["classifier_confidence"]
    assert classifier["n_total"] == 6
    assert classifier["ece"] == pytest.approx(_CLASSIFIER_ECE)
    assert len(classifier["bins"]) == 4
    assert _bin(classifier, 0.0)["count"] == 2
    assert _bin(classifier, 0.0)["mean_confidence"] == pytest.approx(0.0)
    assert _bin(classifier, 0.0)["observed_success_rate"] == pytest.approx(0.5)
    assert _bin(classifier, 0.25)["count"] == 0
    assert _bin(classifier, 0.25)["observed_success_rate"] is None
    assert _bin(classifier, 0.25)["mean_confidence"] is None
    last = _bin(classifier, 0.75)
    assert last["count"] == 2
    assert last["upper"] == pytest.approx(1.0)
    assert last["mean_confidence"] == pytest.approx(0.875)
    assert last["observed_success_rate"] == pytest.approx(0.5)

    agreement = report["agreement_score"]
    assert agreement["n_total"] == 4
    assert agreement["ece"] == pytest.approx(_AGREEMENT_ECE)
    assert _bin(agreement, 0.75)["count"] == 2

    labels = report["ground_truth"]
    assert labels["n_total"] == 3
    assert labels["accuracy"] == pytest.approx(2 / 3)
    assert labels["calibration"]["n_total"] == 3
    assert labels["calibration"]["ece"] == pytest.approx(1 / 6)
    scores = {item["category"]: item for item in labels["per_category"]}
    assert scores["dependency_error"]["precision"] == pytest.approx(0.5)
    assert scores["dependency_error"]["recall"] == pytest.approx(1.0)
    assert scores["dependency_error"]["support"] == 1
    assert scores["test_failure"]["precision"] == pytest.approx(1.0)
    assert scores["test_failure"]["recall"] == pytest.approx(0.5)
    assert scores["test_failure"]["support"] == 2
    assert scores["unknown"]["precision"] is None
    cells = {(cell["predicted"], cell["actual"]): cell["count"] for cell in labels["confusion"]}
    assert cells[("dependency_error", "dependency_error")] == 1
    assert cells[("dependency_error", "test_failure")] == 1
    assert cells[("test_failure", "test_failure")] == 1
    assert cells[("unknown", "unknown")] == 0

    categories = {item["key"]: item for item in by_category.json()["items"]}
    assert categories["dependency_error"]["count"] == 4
    assert categories["dependency_error"]["successes"] == 2
    assert categories["flaky_or_network"]["count"] == 2
    assert categories["flaky_or_network"]["successes"] == 1

    actions = {item["key"]: item for item in by_action.json()["items"]}
    assert actions["fix"]["count"] == 4
    assert actions["fix"]["successes"] == 2
    assert actions["retry"]["count"] == 2
    assert actions["retry"]["successes"] == 1

    outcomes = {item["key"]: item for item in by_outcome.json()["items"]}
    assert outcomes["verified_success"]["count"] == 2
    assert outcomes["verified_failure"]["successes"] == 0
    assert outcomes["retry_success"]["count"] == 1
    assert outcomes["retry_failure"]["count"] == 1

    days = {item["date"]: item for item in timeline.json()["days"]}
    assert days["2024-01-01"]["count"] == 2
    assert days["2024-01-02"]["successes"] == 1
    assert days["2024-01-03"]["count"] == 2
    assert timeline.json()["n_total"] == 6


def test_filters_drop_dry_runs_other_repos_and_dates_outside_the_window() -> None:
    with _client() as client:
        session = client.app.state.session_factory()
        _seed(session)
        session.commit()
        session.close()
        early = client.get("/stats/summary?repo=acme/demo&until=2024-01-03T00:00:00Z", headers=_API)
        other = client.get("/stats/summary?repo=other/demo", headers=_API)
        with_dry = client.get(f"/stats/summary?include_dry_run=true&{_window()}", headers=_API)
        labeled = client.get(
            f"/stats/calibration?bins=4&include_dry_run=true&{_window()}", headers=_API
        )

    # Day 3 is excluded because `until` is exclusive. Two successes remain (day 1 and day 2).
    assert early.json()["n_total"] == 4
    assert early.json()["successes"] == 2
    assert other.json()["n_total"] == 1
    assert other.json()["successes"] == 1
    assert with_dry.json()["n_total"] == 7
    assert with_dry.json()["successes"] == 4
    # The dry-run row is also the extra correct label.
    assert labeled.json()["ground_truth"]["n_total"] == 4
    assert labeled.json()["ground_truth"]["accuracy"] == pytest.approx(0.75)


def test_empty_database_returns_null_rates_and_empty_bins() -> None:
    with _client() as client:
        summary = client.get("/stats/summary", headers=_API)
        report = client.get("/stats/calibration", headers=_API)
        timeline = client.get("/stats/timeline", headers=_API)

    assert summary.json()["n_total"] == 0
    assert summary.json()["success_rate"] is None
    assert summary.json()["accuracy"] is None
    body = report.json()
    assert body["classifier_confidence"]["n_total"] == 0
    assert body["classifier_confidence"]["ece"] is None
    assert len(body["classifier_confidence"]["bins"]) == 10
    assert all(item["count"] == 0 for item in body["classifier_confidence"]["bins"])
    assert body["agreement_score"]["ece"] is None
    assert body["ground_truth"] is None
    assert timeline.json()["days"] == []


def test_confidence_one_is_in_the_last_bin_and_bin_count_is_validated() -> None:
    with _client() as client:
        session = client.app.state.session_factory()
        _seed(session)
        session.commit()
        session.close()
        ok = client.get(f"/stats/calibration?bins=2&{_window()}", headers=_API)
        too_small = client.get("/stats/calibration?bins=1", headers=_API)
        too_big = client.get("/stats/calibration?bins=21", headers=_API)

    bins = ok.json()["classifier_confidence"]["bins"]
    assert len(bins) == 2
    assert bins[-1]["lower"] == pytest.approx(0.5)
    assert bins[-1]["upper"] == pytest.approx(1.0)
    # 0.5, 0.75, and 1.0 are in the last bin. The two 0.0 scores stay in the first.
    assert bins[-1]["count"] == 4
    assert bins[0]["count"] == 2
    assert too_small.status_code == 422
    assert too_big.status_code == 422


def test_stats_require_the_api_token() -> None:
    with _client() as client:
        assert client.get("/stats/summary").status_code == 401
        assert client.get("/stats/calibration").status_code == 401
        assert client.get("/stats/timeline").status_code == 401
        assert client.get("/stats").status_code == 401
