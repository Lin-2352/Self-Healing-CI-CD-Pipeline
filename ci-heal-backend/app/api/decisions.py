"""Decision list. Requires the API token."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.enums import DecisionAction, DecisionOutcome, FailureCategory
from app.db.repositories import DecisionRepository
from app.deps import get_db, require_api_token
from app.schemas.common import Page
from app.schemas.decision import DecisionRead

router = APIRouter(tags=["decisions"], dependencies=[Depends(require_api_token)])


@router.get("/decisions", response_model=Page[DecisionRead])
def list_decisions(
    session: Annotated[Session, Depends(get_db)],
    action: Annotated[DecisionAction | None, Query()] = None,
    category: Annotated[FailureCategory | None, Query()] = None,
    outcome: Annotated[DecisionOutcome | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DecisionRead]:
    """Return a page of decisions, newest first."""
    rows, total = DecisionRepository(session).list_decisions(
        action=action, category=category, outcome=outcome, limit=limit, offset=offset
    )
    return Page(
        items=[DecisionRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
