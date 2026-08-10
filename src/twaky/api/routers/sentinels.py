"""Sentinel framework REST endpoints.

Provides 5 endpoints under /sentinels that expose sentinel observability
and configuration to the Twaky owner.  All routes are protected by the
``require_owner`` dependency (cookie-session auth).

Route order matters for FastAPI path disambiguation:
  - ``GET /sentinels/runs/{run_id}`` must be declared BEFORE
    ``GET /sentinels/{name}`` so that the literal segment "runs" is matched
    first and never captured as a ``name`` path parameter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import jsonschema
from fastapi import APIRouter, Depends, Query

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.sentinels import (
    SentinelDetail,
    SentinelPatch,
    SentinelRunDetail,
    SentinelRunSummary,
    SentinelSummary,
    Stats24h,
)
from twaky.sentinels import repository
from twaky.sentinels.repository import SentinelNotFound

router = APIRouter(prefix="/sentinels", tags=["sentinels"])


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _to_detail(cfg) -> SentinelDetail:
    return SentinelDetail(
        name=cfg.name,
        display_name=cfg.display_name,
        description=cfg.description,
        enabled=cfg.enabled,
        version=cfg.version,
        config_schema=cfg.config_schema,
        config_values=cfg.config_values,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


def _to_run_summary(run) -> SentinelRunSummary:
    return SentinelRunSummary(
        id=run.id,
        sentinel_name=run.sentinel_name,
        event_ref=run.event_ref,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        outcome=run.outcome,
        mission_id=run.mission_id,
        llm_calls=run.llm_calls,
    )


def _to_run_detail(run) -> SentinelRunDetail:
    return SentinelRunDetail(
        id=run.id,
        sentinel_name=run.sentinel_name,
        event_ref=run.event_ref,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        outcome=run.outcome,
        mission_id=run.mission_id,
        llm_calls=run.llm_calls,
        trace=run.trace if run.trace is not None else [],
        error_repr=run.error_repr,
    )


# ---------------------------------------------------------------------------
# 1. GET /sentinels
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SentinelSummary])
def list_sentinels(
    _email: str = Depends(require_owner),
) -> list[SentinelSummary]:
    """Return all sentinels with 24-hour run statistics."""
    sentinels = repository.list_all()
    result: list[SentinelSummary] = []
    for cfg in sentinels:
        total, errors = repository.count_runs_24h(cfg.name)
        result.append(
            SentinelSummary(
                name=cfg.name,
                display_name=cfg.display_name,
                enabled=cfg.enabled,
                version=cfg.version,
                stats_24h=Stats24h(total=total, errors=errors),
            )
        )
    return result


# ---------------------------------------------------------------------------
# 5. GET /sentinels/runs/{run_id}  — declared BEFORE /{name} to avoid clash
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}", response_model=SentinelRunDetail)
def get_run_detail(
    run_id: UUID,
    _email: str = Depends(require_owner),
):
    """Return the full detail for a single sentinel run."""
    run = repository.get_run(run_id)
    if run is None:
        return error_response(
            code="sentinel_run_not_found",
            message=f"sentinel run {run_id} not found",
            status_code=404,
        )
    return _to_run_detail(run)


# ---------------------------------------------------------------------------
# 2. GET /sentinels/{name}
# ---------------------------------------------------------------------------


@router.get("/{name}", response_model=SentinelDetail)
def get_sentinel(
    name: str,
    _email: str = Depends(require_owner),
):
    """Return the full detail for a single sentinel."""
    cfg = repository.get(name)
    if cfg is None:
        return error_response(
            code="sentinel_not_found",
            message=f"sentinel {name!r} not found",
            status_code=404,
        )
    return _to_detail(cfg)


# ---------------------------------------------------------------------------
# 3. PATCH /sentinels/{name}
# ---------------------------------------------------------------------------


@router.patch("/{name}", response_model=SentinelDetail)
def patch_sentinel(
    name: str,
    body: SentinelPatch,
    _email: str = Depends(require_owner),
):
    """Toggle enabled state or update config_values for a sentinel.

    If ``config_values`` is provided, it is validated against the sentinel's
    ``config_schema`` using JSON Schema Draft 2020-12 before persisting.
    Returns 422 with code ``validation_failed`` on schema violations.
    """
    provided = body.model_dump(exclude_unset=True)
    if not provided:
        return error_response(
            code="validation_failed",
            message="at least one field must be provided",
            status_code=422,
        )

    # Validate config_values against the persisted config_schema
    if "config_values" in provided:
        cfg = repository.get(name)
        if cfg is None:
            return error_response(
                code="sentinel_not_found",
                message=f"sentinel {name!r} not found",
                status_code=404,
            )
        schema = cfg.config_schema
        if schema:
            validator = jsonschema.Draft202012Validator(schema)
            errors = list(validator.iter_errors(provided["config_values"]))
            if errors:
                error_messages = [e.message for e in errors]
                return error_response(
                    code="validation_failed",
                    message="config_values failed schema validation",
                    detail={"errors": error_messages},
                    status_code=422,
                )

    try:
        fresh = repository.update(name, provided)
    except SentinelNotFound:
        return error_response(
            code="sentinel_not_found",
            message=f"sentinel {name!r} not found",
            status_code=404,
        )
    return _to_detail(fresh)


# ---------------------------------------------------------------------------
# 4. GET /sentinels/{name}/runs
# ---------------------------------------------------------------------------


@router.get("/{name}/runs", response_model=list[SentinelRunSummary])
def list_runs(
    name: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: Annotated[datetime | None, Query()] = None,
    _email: str = Depends(require_owner),
):
    """List sentinel runs, newest first.

    Accepts optional pagination parameters:
    - ``limit``: 1..500 (default 100)
    - ``before``: ISO-8601 timestamp cursor
    """
    # Verify the sentinel exists first
    cfg = repository.get(name)
    if cfg is None:
        return error_response(
            code="sentinel_not_found",
            message=f"sentinel {name!r} not found",
            status_code=404,
        )
    runs = repository.list_runs(sentinel_name=name, limit=limit, before=before)
    return [_to_run_summary(r) for r in runs]


__all__ = ["router"]
