"""Skills configuration routes.

This file is grown across T10 (GET), T11 (POST/PATCH/DELETE), T12 (test).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.skills import Skill, SkillCreate, SkillSummary, SkillUpdate
from twaky.skills_config import repository, service
from twaky.skills_config.repository import SkillNameConflict, SkillNotFound
from twaky.skills_config.service import ValidationError

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


@router.post("", response_model=Skill, status_code=status.HTTP_201_CREATED)
def create_skill(body: SkillCreate, _email: str = Depends(require_owner)):
    try:
        norm = service.validate_create(body.model_dump())
    except ValidationError as exc:
        return error_response(
            code="validation_failed",
            message=exc.message,
            detail={"field": exc.field},
            status_code=422,
        )
    try:
        sk = repository.create(**norm)
    except SkillNameConflict:
        return error_response(
            code="validation_failed",
            message=f"a skill named {norm['name']!r} already exists",
            detail={"field": "name"},
            status_code=422,
        )
    return _to_full(sk)


@router.patch("/{skill_id}", response_model=Skill)
def patch_skill(
    skill_id: UUID,
    body: SkillUpdate,
    _email: str = Depends(require_owner),
):
    provided = body.model_dump(exclude_unset=True)
    try:
        patch = service.validate_patch(provided)
    except ValidationError as exc:
        return error_response(
            code="validation_failed",
            message=exc.message,
            detail={"field": exc.field},
            status_code=422,
        )
    try:
        fresh = repository.update(skill_id, patch)
    except SkillNotFound:
        return error_response(
            code="skill_not_found",
            message=f"skill {skill_id} not found",
            status_code=404,
        )
    except SkillNameConflict:
        return error_response(
            code="validation_failed",
            message=f"a skill named {patch.get('name')!r} already exists",
            detail={"field": "name"},
            status_code=422,
        )
    return _to_full(fresh)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_id: UUID, _email: str = Depends(require_owner)) -> Response:
    if not repository.delete(skill_id):
        return error_response(
            code="skill_not_found",
            message=f"skill {skill_id} not found",
            status_code=404,
        )
    return Response(status_code=204)


__all__ = ["router"]
