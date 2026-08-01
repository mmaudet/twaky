"""Mission CRUD routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from twaky.api.deps import require_owner
from twaky.missions import engine, repository
from twaky.missions.models import Mission, MissionState

router = APIRouter(prefix="/missions", tags=["missions"])


class DeclareBody(BaseModel):
    intent_text: str = Field(min_length=1, max_length=4096)


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


__all__ = ["router"]
