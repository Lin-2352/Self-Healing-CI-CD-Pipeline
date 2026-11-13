"""Operator stats. Every route requires the API token.

Success is a FIX whose outcome is `verified_success`, or a RETRY whose outcome is
`retry_success`. Pending and error outcomes are left out. Dry runs are left out
unless `include_dry_run` is true. `since` is inclusive and `until` is exclusive,
both compared with `created_at`.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.models import Decision
from app.db.stats import StatsFilter, StatsRepository
from app.deps import get_db, require_api_token
from app.schemas.stats import CalibrationReport, GroupedStats, Summary, Timeline

router = APIRouter(tags=["stats"], dependencies=[Depends(require_api_token)])


def _bounds(
    repo: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    include_dry_run: Annotated[bool, Query()] = False,
) -> StatsFilter:
    """Read the filters shared by every stats route."""
    return StatsFilter(repo=repo, since=since, until=until, include_dry_run=include_dry_run)


@router.get("/stats", response_model=Summary)
@router.get("/stats/summary", response_model=Summary)
def summary(
    session: Annotated[Session, Depends(get_db)],
    bounds: Annotated[StatsFilter, Depends(_bounds)],
) -> Summary:
    """Return how many resolved decisions succeeded, and labeled accuracy when present."""
    return StatsRepository(session).summary(bounds)


@router.get("/stats/by-category", response_model=GroupedStats)
def by_category(
    session: Annotated[Session, Depends(get_db)],
    bounds: Annotated[StatsFilter, Depends(_bounds)],
) -> GroupedStats:
    """Group resolved decisions by the category the classifier stored."""
    return StatsRepository(session).grouped(bounds, Decision.category)


@router.get("/stats/by-action", response_model=GroupedStats)
def by_action(
    session: Annotated[Session, Depends(get_db)],
    bounds: Annotated[StatsFilter, Depends(_bounds)],
) -> GroupedStats:
    """Group resolved decisions by retry, fix, or escalate."""
    return StatsRepository(session).grouped(bounds, Decision.action)


@router.get("/stats/by-outcome", response_model=GroupedStats)
def by_outcome(
    session: Annotated[Session, Depends(get_db)],
    bounds: Annotated[StatsFilter, Depends(_bounds)],
) -> GroupedStats:
    """Group resolved decisions by how they ended."""
    return StatsRepository(session).grouped(bounds, Decision.outcome)


@router.get("/stats/calibration", response_model=CalibrationReport)
def calibration(
    session: Annotated[Session, Depends(get_db)],
    bounds: Annotated[StatsFilter, Depends(_bounds)],
    bins: Annotated[int, Query(ge=2, le=20)] = 10,
) -> CalibrationReport:
    """Bin classifier confidence and agreement, and report ground truth when it exists.

    Confidence 1.0 is in the last bin. Each series includes `n_total` and ECE.
    ECE is the sum of `n_b / N * |observed_success_rate - mean_confidence|`.
    """
    return StatsRepository(session).calibration(bounds, bins)


@router.get("/stats/timeline", response_model=Timeline)
def timeline(
    session: Annotated[Session, Depends(get_db)],
    bounds: Annotated[StatsFilter, Depends(_bounds)],
) -> Timeline:
    """Return resolved decisions per UTC day."""
    return StatsRepository(session).timeline(bounds)
