"""Mail-sentinel vertical REST endpoints.

Provides 8 endpoints under /mail-sentinel that expose rules CRUD,
memories list, and learned patterns list + forget to the Twaky owner.
All routes are protected by the ``require_owner`` dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

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
    MailRuleProposeRequest,
    MailRuleProposeResponse,
    MailRuleSummary,
    MatchedExample,
)
from twaky.sentinels.mail.nodes import rule_matches_static
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import rules as rules_store
from twaky.sentinels.mail.store import spam_decisions
from twaky.sentinels.mail.store.rules import MailRule, RuleValidationError

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


# ---------------------------------------------------------------------------
# 9. POST /mail-sentinel/rules/propose  (SP6d)
# ---------------------------------------------------------------------------

_MAX_EXAMPLES = 10


def _conditions_use_header_field(conditions: list[dict]) -> bool:
    """Return True if any condition in *conditions* targets a ``header:*`` field."""
    return any(c.get("field", "").startswith("header:") for c in conditions)


def _conditions_use_body_field(conditions: list[dict]) -> bool:
    """Return True if any condition in *conditions* targets the ``body`` field."""
    return any(c.get("field", "") == "body" for c in conditions)


def _build_jmap_envelope_from_decision(decision) -> dict:
    """Build a minimal JMAP-like email dict from a SpamDecision for rule matching.

    ``rule_matches_static`` (and its helpers) expect a JMAP email dict with
    ``from`` as a list of ``{"email": ..., "name": ...}`` dicts, ``subject`` as
    a string, and ``headers`` as a list of ``{"name": ..., "value": ...}``
    dicts.  Body-related fields are intentionally empty — the simulation
    does not fetch email bodies from JMAP.
    """
    jmap_from = [{"email": decision.sender_email, "name": ""}]

    jmap_headers = []
    if decision.envelope_headers:
        for name, value in decision.envelope_headers.items():
            jmap_headers.append({"name": name, "value": value})

    return {
        "from": jmap_from,
        "subject": decision.subject or "",
        "headers": jmap_headers,
        # Body fields are empty — simulation limitation.
        "to": [],
        "bodyValues": {},
        "textBody": [],
        "preview": "",
    }


@router.post("/rules/propose", response_model=MailRuleProposeResponse)
def propose_rule(
    body: MailRuleProposeRequest,
    _email: str = Depends(require_owner),
):
    """Simulate a proposed rule against historical spam decisions.

    Returns a preview of how many recent decisions would have matched the
    proposed rule, which existing rules would shadow it, and up to 10 example
    rows — without persisting anything.
    """
    # ------------------------------------------------------------------
    # 1. Validate the rule via the same store validators the create
    #    endpoint uses (name, conditions, actions, combinator).
    # ------------------------------------------------------------------
    try:
        rules_store.validate_name(body.name)
        rules_store.validate_conditions(body.conditions)
        rules_store.validate_actions(body.actions)
        if body.combinator not in ("OR", "AND"):
            raise RuleValidationError(
                f"combinator must be 'OR' or 'AND', got {body.combinator!r}"
            )
    except RuleValidationError as exc:
        return error_response(
            code="validation_failed",
            message=str(exc),
            status_code=422,
        )

    # ------------------------------------------------------------------
    # 2. Load historical decisions.
    # ------------------------------------------------------------------
    decisions = spam_decisions.list_recent(bucket=None, limit=body.window.count)

    # ------------------------------------------------------------------
    # 4. Load earlier-priority enabled rules (priority < proposed priority).
    # ------------------------------------------------------------------
    all_enabled_rules = rules_store.list_all(enabled_only=True)
    earlier_rules = [r for r in all_enabled_rules if r.priority < body.priority]

    # ------------------------------------------------------------------
    # 5. Build a synthetic MailRule for the proposed rule so we can
    #    pass it directly to rule_matches_static.
    # ------------------------------------------------------------------
    _now = datetime.now(UTC)
    proposed_rule = MailRule(
        id=uuid4(),
        name=body.name,
        description="",
        conditions=body.conditions,
        combinator=body.combinator,
        actions=body.actions,
        priority=body.priority,
        enabled=body.enabled,
        run_on_threads=True,
        created_at=_now,
        updated_at=_now,
    )

    # Pre-compute which field types the proposed rule uses.
    proposed_uses_header = _conditions_use_header_field(body.conditions)
    proposed_uses_body = _conditions_use_body_field(body.conditions)

    # ------------------------------------------------------------------
    # 6. Replay the proposed rule against each historical decision.
    # ------------------------------------------------------------------
    matched_count = 0
    would_shadow_count = 0
    matched_examples: list[MatchedExample] = []
    would_shadow_set: set[str] = set()

    # Partial-reason tracking (using a set to deduplicate).
    partial_reasons: set[str] = set()

    for decision in decisions:
        null_headers = decision.envelope_headers is None
        jmap_envelope = _build_jmap_envelope_from_decision(decision)

        # When the proposed rule uses a header field, flag partial whenever we
        # evaluate it against a decision whose headers were not captured — the
        # null headers may have prevented a match that would otherwise have
        # fired, making the simulation unreliable for that row.
        if proposed_uses_header and null_headers:
            partial_reasons.add("header rules not evaluated")

        # Body fields are always evaluated against empty body/preview in the
        # simulation (we do not fetch email bodies from JMAP).
        if proposed_uses_body:
            partial_reasons.add("body predicate not evaluated")

        verdict = rule_matches_static(jmap_envelope, proposed_rule)
        if verdict is not True:
            continue

        # This decision matched the proposed rule.
        matched_count += 1

        # Check whether any earlier-priority rule would shadow this match.
        shadow_rule_name: str | None = None
        for earlier in earlier_rules:
            earlier_verdict = rule_matches_static(jmap_envelope, earlier)
            if earlier_verdict is True:
                shadow_rule_name = earlier.name
                would_shadow_set.add(earlier.name)
                # Accumulate partial reasons for shadow rules that DID fire on a
                # matched decision — per finding #2 (do not over-trigger for
                # earlier rules that never matched any decision).
                if _conditions_use_header_field(earlier.conditions) and null_headers:
                    partial_reasons.add("header rules not evaluated")
                if _conditions_use_body_field(earlier.conditions):
                    partial_reasons.add("body predicate not evaluated")
                break

        if shadow_rule_name is not None:
            would_shadow_count += 1

        if len(matched_examples) < _MAX_EXAMPLES:
            matched_examples.append(
                MatchedExample(
                    decision_id=str(decision.id),
                    sender=decision.sender_email,
                    subject=decision.subject or "",
                    current_bucket=decision.bucket,
                    would_shadow_by=shadow_rule_name,
                )
            )

    # ------------------------------------------------------------------
    # 7. Compute simulation_partial and reason string.
    # ------------------------------------------------------------------
    simulation_partial = len(partial_reasons) > 0
    simulation_partial_reason: str | None = None
    if simulation_partial:
        simulation_partial_reason = "; ".join(sorted(partial_reasons))

    return MailRuleProposeResponse(
        valid=True,
        matched_count=matched_count,
        would_shadow_count=would_shadow_count,
        matched_examples=matched_examples,
        would_shadow=sorted(would_shadow_set),
        simulation_partial=simulation_partial,
        simulation_partial_reason=simulation_partial_reason,
    )


__all__ = ["router"]
