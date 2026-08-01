"""Mission CRUD routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.config import settings as _settings
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
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
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
    if refetched is None:
        raise HTTPException(status_code=500, detail="mission vanished after transition")
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
    if refetched is None:
        raise HTTPException(status_code=500, detail="mission vanished after transition")
    return refetched


@router.get("/{mid}/trace")
def mission_trace(mid: UUID, email: str = Depends(require_owner)):
    """Redirect to the mission's Langfuse session."""
    m = repository.get(mid)
    if m is None or m.owner_email != email:
        raise HTTPException(status_code=404, detail="mission not found")
    host = getattr(_settings, "langfuse_host", "") or ""
    project_id = getattr(_settings, "langfuse_project_id", "") or ""
    if not host or not project_id:
        return error_response(
            code="langfuse_not_configured",
            message="langfuse host or project id not set",
            status_code=503,
        )
    url = f"{host.rstrip('/')}/project/{project_id}/sessions/mission-{mid}"
    return RedirectResponse(url=url, status_code=302)


__all__ = ["router"]
