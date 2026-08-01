"""Mission CRUD routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from twaky.api.deps import require_owner
from twaky.missions import engine, repository
from twaky.missions.guards import InvalidTransition
from twaky.missions.models import Mission, MissionState

router = APIRouter(prefix="/missions", tags=["missions"])


class DeclareBody(BaseModel):
    intent_text: str = Field(min_length=1, max_length=4096)


class ResumeBody(BaseModel):
    user_response: dict[str, Any]


class CancelBody(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


@router.post("", status_code=201, response_model=Mission)
def declare(body: DeclareBody, email: str = Depends(require_owner)) -> Mission:
    """Declare a new mission. The daemon will pick it up via NOTIFY."""
    return engine.declare(
        intent_text=body.intent_text,
        owner_email=email,
        declared_by=email,
    )


@router.get("", response_model=list[Mission])
def list_missions(
    state: Annotated[MissionState | None, Query()] = None,
    limit: int = 50,
    offset: int = 0,
    email: str = Depends(require_owner),
) -> list[Mission]:
    """List live missions for the instance owner."""
    rows = repository.list_live(email)
    if state is not None:
        rows = [r for r in rows if r.state == state]
    return rows[offset : offset + limit]


@router.get("/{mid}", response_model=Mission)
def get_mission(mid: UUID, email: str = Depends(require_owner)) -> Mission:
    """Get a single mission by ID."""
    m = repository.get(mid)
    if m is None or m.owner_email != email:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


@router.post("/{mid}/resume", response_model=Mission)
def resume_mission(
    mid: UUID, body: ResumeBody, email: str = Depends(require_owner)
) -> Mission:
    """Resume a mission awaiting user input."""
    m = repository.get(mid)
    if m is None or m.owner_email != email:
        raise HTTPException(status_code=404, detail="mission not found")
    try:
        engine.resume(mid, user_response=body.user_response)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    refetched = repository.get(mid)
    assert refetched is not None  # engine call succeeded, mission must exist
    return refetched


@router.post("/{mid}/cancel", response_model=Mission)
def cancel_mission(
    mid: UUID, body: CancelBody, email: str = Depends(require_owner)
) -> Mission:
    """Cancel a mission."""
    m = repository.get(mid)
    if m is None or m.owner_email != email:
        raise HTTPException(status_code=404, detail="mission not found")
    try:
        engine.cancel(mid, reason=body.reason)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    refetched = repository.get(mid)
    assert refetched is not None  # engine call succeeded, mission must exist
    return refetched


__all__ = ["router"]
