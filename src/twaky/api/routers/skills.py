"""Skills configuration routes.

This file is grown across T10 (GET), T11 (POST/PATCH/DELETE), T12 (test).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.skills import Skill, SkillSummary
from twaky.skills_config import repository

router = APIRouter(prefix="/skills", tags=["skills"])


def _to_summary(sk) -> SkillSummary:
    return SkillSummary(
        id=sk.id,
        name=sk.name,
        description=sk.description,
        bound_agents=sk.bound_agents,
        enabled=sk.enabled,
        created_at=sk.created_at,
        updated_at=sk.updated_at,
    )


def _to_full(sk) -> Skill:
    return Skill(
        id=sk.id,
        name=sk.name,
        description=sk.description,
        python_source=sk.python_source,
        config_schema=sk.config_schema,
        config_values=sk.config_values,
        bound_agents=sk.bound_agents,
        enabled=sk.enabled,
        created_at=sk.created_at,
        updated_at=sk.updated_at,
    )


@router.get("", response_model=list[SkillSummary])
def list_skills(_email: str = Depends(require_owner)) -> list[SkillSummary]:
    return [_to_summary(s) for s in repository.list_all()]


@router.get("/{skill_id}", response_model=Skill)
def get_skill(skill_id: UUID, _email: str = Depends(require_owner)):
    sk = repository.get(skill_id)
    if sk is None:
        return error_response(
            code="skill_not_found",
            message=f"skill {skill_id} not found",
            status_code=404,
        )
    return _to_full(sk)


__all__ = ["router"]
