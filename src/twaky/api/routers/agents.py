"""Agent configuration routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from twaky.agents.defaults import DEFAULT_PROMPTS
from twaky.agents_config import repository
from twaky.agents_config.repository import AgentConfigNotFound
from twaky.agents_config.service import ValidationError, effective_model, validate_patch
from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.agents import (
    Agent,
    AgentSummary,
    AgentUpdate,
    DefaultPromptResponse,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_summary(cfg) -> AgentSummary:
    return AgentSummary(
        id=cfg.id,
        display_name=cfg.display_name,
        role=cfg.role,
        model=cfg.model,
        temperature=cfg.temperature,
        effective_model=effective_model(cfg),
        updated_at=cfg.updated_at,
    )


def _to_full(cfg) -> Agent:
    return Agent(
        id=cfg.id,
        display_name=cfg.display_name,
        role=cfg.role,
        system_prompt=cfg.system_prompt,
        model=cfg.model,
        temperature=cfg.temperature,
        effective_model=effective_model(cfg),
        updated_at=cfg.updated_at,
    )


@router.get("", response_model=list[AgentSummary])
def list_agents(_email: str = Depends(require_owner)) -> list[AgentSummary]:
    return [_to_summary(c) for c in repository.list_all()]


@router.get("/{agent_id}", response_model=Agent)
def get_agent(agent_id: str, _email: str = Depends(require_owner)):
    cfg = repository.get(agent_id)
    if cfg is None:
        return error_response(
            code="agent_not_found",
            message=f"agent {agent_id!r} not found",
            status_code=404,
        )
    return _to_full(cfg)


@router.get("/{agent_id}/default_prompt", response_model=DefaultPromptResponse)
def get_default_prompt(agent_id: str, _email: str = Depends(require_owner)):
    if agent_id not in DEFAULT_PROMPTS:
        return error_response(
            code="agent_not_found",
            message=f"agent {agent_id!r} not found",
            status_code=404,
        )
    return DefaultPromptResponse(system_prompt=DEFAULT_PROMPTS[agent_id])


@router.patch("/{agent_id}", response_model=Agent)
def patch_agent(
    agent_id: str,
    body: AgentUpdate,
    _email: str = Depends(require_owner),
):
    # AgentUpdate accepts all-null; enforce the "at least one field required"
    # invariant here rather than in pydantic (which can't distinguish
    # "explicit null" from "field omitted" without model_fields_set).
    provided = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    try:
        patch = validate_patch(provided)
    except ValidationError as exc:
        return error_response(
            code="validation_failed",
            message=exc.message,
            status_code=422,
        )
    try:
        fresh = repository.update(agent_id, patch)
    except AgentConfigNotFound:
        return error_response(
            code="agent_not_found",
            message=f"agent {agent_id!r} not found",
            status_code=404,
        )
    return _to_full(fresh)


__all__ = ["router"]
