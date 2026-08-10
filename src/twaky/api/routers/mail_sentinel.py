"""Mail-sentinel vertical REST endpoints.

Provides 8 endpoints under /mail-sentinel that expose rules CRUD,
memories list, and learned patterns list + forget to the Twaky owner.
All routes are protected by the ``require_owner`` dependency.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from starlette.responses import Response

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.mail_sentinel import (
    LearnedPatternSummary,
    MailMemorySummary,
    MailRuleCreate,
    MailRuleDetail,
    MailRulePatch,
    MailRuleSummary,
)
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import rules as rules_store
from twaky.sentinels.mail.store.rules import RuleValidationError

router = APIRouter(prefix="/mail-sentinel", tags=["mail-sentinel"])


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _rule_to_summary(rule) -> MailRuleSummary:
    return MailRuleSummary(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        priority=rule.priority,
        enabled=rule.enabled,
        run_on_threads=rule.run_on_threads,
        action_count=len(rule.actions),
        condition_count=len(rule.conditions),
    )


def _rule_to_detail(rule) -> MailRuleDetail:
    return MailRuleDetail(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        priority=rule.priority,
        enabled=rule.enabled,
        run_on_threads=rule.run_on_threads,
        action_count=len(rule.actions),
        condition_count=len(rule.conditions),
        conditions=rule.conditions,
        combinator=rule.combinator,
        actions=rule.actions,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _memory_to_summary(mem) -> MailMemorySummary:
    return MailMemorySummary(
        id=mem.id,
        kind=mem.kind,
        scope=mem.scope,
        scope_value=mem.scope_value,
        content=mem.content,
        created_at=mem.created_at,
        expires_at=mem.expires_at,
    )


def _pattern_to_summary(pat) -> LearnedPatternSummary:
    return LearnedPatternSummary(
        id=str(pat.id),
        sender_email=pat.sender_email,
        rule_name=pat.rule_name,
        confidence=float(pat.confidence),
        evidence_count=pat.evidence_count,
        first_seen=pat.first_seen,
        last_confirmed=pat.last_confirmed,
        is_active=pat.is_active,
    )


# ---------------------------------------------------------------------------
# 1. GET /mail-sentinel/rules
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=list[MailRuleSummary])
def list_rules(
    enabled: bool | None = None,
    _email: str = Depends(require_owner),
) -> list[MailRuleSummary]:
    """Return all rules ordered by priority ASC.

    When ``enabled=true``, delegates to ``list_all(enabled_only=True)`` to
    filter at the DB level.  When absent or ``enabled=false``, fetches all
    rows and filters client-side so the FE can decide.
    """
    if enabled is True:
        rules = rules_store.list_all(enabled_only=True)
    else:
        rules = rules_store.list_all(enabled_only=False)
        if enabled is False:
            rules = [r for r in rules if not r.enabled]
    return [_rule_to_summary(r) for r in rules]


# ---------------------------------------------------------------------------
# 2. POST /mail-sentinel/rules
# ---------------------------------------------------------------------------


@router.post("/rules", response_model=MailRuleDetail, status_code=201)
def create_rule(
    body: MailRuleCreate,
    _email: str = Depends(require_owner),
):
    """Create a new mail rule with full validation.

    Returns 422 with code ``validation_failed`` on service-layer rejections.
    """
    try:
        rule = rules_store.create(
            name=body.name,
            description=body.description,
            conditions=body.conditions,
            combinator=body.combinator,
            actions=body.actions,
            priority=body.priority,
            enabled=body.enabled,
            run_on_threads=body.run_on_threads,
        )
    except RuleValidationError as exc:
        return error_response(
            code="validation_failed",
            message=str(exc),
            status_code=422,
        )
    return _rule_to_detail(rule)


# ---------------------------------------------------------------------------
# 3. GET /mail-sentinel/rules/{id}
# ---------------------------------------------------------------------------


@router.get("/rules/{rule_id}", response_model=MailRuleDetail)
def get_rule(
    rule_id: UUID,
    _email: str = Depends(require_owner),
):
    """Return the full detail for a single rule."""
    rule = rules_store.get(rule_id)
    if rule is None:
        return error_response(
            code="mail_rule_not_found",
            message=f"mail rule {rule_id} not found",
            status_code=404,
        )
    return _rule_to_detail(rule)


# ---------------------------------------------------------------------------
# 4. PATCH /mail-sentinel/rules/{id}
# ---------------------------------------------------------------------------


@router.patch("/rules/{rule_id}", response_model=MailRuleDetail)
def patch_rule(
    rule_id: UUID,
    body: MailRulePatch,
    _email: str = Depends(require_owner),
):
    """Apply a partial patch to a rule.

    Returns 422 with ``validation_failed`` on empty body or invalid fields.
    Returns 404 with ``mail_rule_not_found`` if the rule does not exist.
    """
    provided = body.model_dump(exclude_unset=True)
    if not provided:
        return error_response(
            code="validation_failed",
            message="at least one field must be provided",
            status_code=422,
        )
    try:
        rule = rules_store.update(rule_id, provided)
    except RuleValidationError as exc:
        return error_response(
            code="validation_failed",
            message=str(exc),
            status_code=422,
        )
    except KeyError:
        return error_response(
            code="mail_rule_not_found",
            message=f"mail rule {rule_id} not found",
            status_code=404,
        )
    return _rule_to_detail(rule)


# ---------------------------------------------------------------------------
# 5. DELETE /mail-sentinel/rules/{id}
# ---------------------------------------------------------------------------


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: UUID,
    _email: str = Depends(require_owner),
):
    """Delete a rule by id. Returns 404 if not found."""
    try:
        rules_store.delete(rule_id)
    except KeyError:
        return error_response(
            code="mail_rule_not_found",
            message=f"mail rule {rule_id} not found",
            status_code=404,
        )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 6. GET /mail-sentinel/memories
# ---------------------------------------------------------------------------


@router.get("/memories", response_model=list[MailMemorySummary])
def list_memories(
    scope: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    _email: str = Depends(require_owner),
) -> list[MailMemorySummary]:
    """Return recently created memories, optionally filtered by scope."""
    memories = mem_store.list_recent(scope=scope, limit=limit)
    return [_memory_to_summary(m) for m in memories]


# ---------------------------------------------------------------------------
# 7. GET /mail-sentinel/learned-patterns
# ---------------------------------------------------------------------------


@router.get("/learned-patterns", response_model=list[LearnedPatternSummary])
def list_learned_patterns(
    active_only: bool = False,
    _email: str = Depends(require_owner),
) -> list[LearnedPatternSummary]:
    """Return all learned patterns, optionally filtered to active ones."""
    patterns = lp_store.list_all(active_only=active_only)
    return [_pattern_to_summary(p) for p in patterns]


# ---------------------------------------------------------------------------
# 8. DELETE /mail-sentinel/learned-patterns/{sender_email}/{rule_name}
# ---------------------------------------------------------------------------


@router.delete("/learned-patterns/{sender_email}/{rule_name}", status_code=204)
def forget_pattern(
    sender_email: str,
    rule_name: str,
    _email: str = Depends(require_owner),
):
    """Forget a learned pattern. Silently succeeds even if the row does not exist."""
    lp_store.forget(sender_email, rule_name)
    return Response(status_code=204)


__all__ = ["router"]
