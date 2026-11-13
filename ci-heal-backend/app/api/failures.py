"""Failure list, detail, and reprocess. Requires the API token."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.enums import ProcessingStatus
from app.db.repositories import FailureRepository
from app.deps import get_db, require_api_token
from app.schemas.common import Page
from app.schemas.failure import FailureDetail, FailureRead
from app.services.orchestrator import process_failure

router = APIRouter(tags=["failures"], dependencies=[Depends(require_api_token)])


@router.get("/failures", response_model=Page[FailureRead])
def list_failures(
    session: Annotated[Session, Depends(get_db)],
    repo: Annotated[str | None, Query()] = None,
    processing_status: Annotated[ProcessingStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FailureRead]:
    """Return a page of failures, newest first."""
    rows, total = FailureRepository(session).list_failures(
        repo=repo, processing_status=processing_status, limit=limit, offset=offset
    )
    return Page(
        items=[FailureRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/failures/{failure_id}", response_model=FailureDetail)
def get_failure(
    failure_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> FailureDetail:
    """Return one failure with its classifications, patches, and decisions."""
    failure = FailureRepository(session).get(failure_id)
    if failure is None:
        raise HTTPException(status_code=404, detail="not found")
    return FailureDetail.model_validate(failure)


@router.post("/failures/{failure_id}/reprocess", status_code=202)
def reprocess_failure(
    failure_id: int,
    request: Request,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_db)],
    force: Annotated[bool, Query()] = False,
) -> dict[str, str]:
    """Queue a failure again. A row that is currently processing is refused.

    A finished failure stays finished unless `force` is true. An errored failure
    returns to the queue. Either path clears `recovery_count`, so an operator retry
    gets a fresh startup budget. The claim lets only one worker take a queued row.
    """
    failures = FailureRepository(session)
    failure = failures.get(failure_id)
    if failure is None:
        raise HTTPException(status_code=404, detail="not found")
    if failure.processing_status is ProcessingStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="failure is processing")
    if failure.processing_status is ProcessingStatus.DONE and not force:
        raise HTTPException(status_code=409, detail="failure is done")
    if failure.processing_status is ProcessingStatus.ERROR:
        failures.transition_status(failure, ProcessingStatus.QUEUED)
    elif failure.processing_status is ProcessingStatus.DONE:
        failures.transition_status(failure, ProcessingStatus.QUEUED, force=True)
    failure.recovery_count = 0
    session.commit()
    state = request.app.state
    background.add_task(
        process_failure,
        failure.id,
        settings=state.settings,
        session_factory=state.session_factory,
        github=state.github,
        classifier_llm=state.classifier_llm,
        fixer_llm=state.fixer_llm,
    )
    return {"status": "queued"}
