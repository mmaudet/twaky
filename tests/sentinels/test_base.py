"""Unit tests for twaky.sentinels.base — no DB required.

Covers:
- Outcome enum values match the DB CHECK constraint (regression guard
  referencing sql/008_init_sentinels.sh T1 CHECK constraint).
- Sentinel ABC cannot be instantiated directly.
- Subclass missing process() raises TypeError on instantiation.
- Default should_process() returns True.
- Context dataclass has the expected fields.
- Event TypedDict happy-path assignment works.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

from twaky.sentinels.base import Context, Event, Outcome, Sentinel

# ---------------------------------------------------------------------------
# Outcome enum — regression guard against T1 DB CHECK constraint drift
# ---------------------------------------------------------------------------

# The DB CHECK constraint in sql/008_init_sentinels.sh reads:
#   outcome IN ('ignored','processed','mission_created','delegated','error')
# If Outcome drifts from this set this test fails — fix the enum, not the test.
_DB_CHECK_SET = {"ignored", "processed", "mission_created", "delegated", "error"}


def test_outcome_values_match_db_check_constraint():
    """Regression: Outcome enum values must equal the T1 CHECK set exactly."""
    assert {o.value for o in Outcome} == _DB_CHECK_SET


def test_outcome_is_str_subclass():
    assert isinstance(Outcome.IGNORED, str)
    assert Outcome.MISSION_CREATED == "mission_created"


def test_outcome_individual_values():
    assert Outcome.IGNORED.value == "ignored"
    assert Outcome.PROCESSED.value == "processed"
    assert Outcome.MISSION_CREATED.value == "mission_created"
    assert Outcome.DELEGATED.value == "delegated"
    assert Outcome.ERROR.value == "error"


# ---------------------------------------------------------------------------
# Sentinel ABC
# ---------------------------------------------------------------------------


def test_sentinel_cannot_be_instantiated_directly():
    """ABC must reject direct instantiation."""
    with pytest.raises(TypeError):
        Sentinel()  # type: ignore[abstract]


def test_sentinel_subclass_missing_process_raises_type_error():
    """Subclass that does not implement process() must raise on instantiation."""

    class IncompleteS(Sentinel):
        name = "incomplete"
        version = "0.0.1"
        event_source_kind = "rabbitmq"
        # process() intentionally omitted

    with pytest.raises(TypeError):
        IncompleteS()


def test_sentinel_subclass_with_process_instantiates():
    """A concrete subclass that implements process() must be instantiable."""

    class GoodS(Sentinel):
        name = "good"
        version = "1.0.0"
        event_source_kind = "rabbitmq"

        def process(self, event: Event, ctx: Context) -> Outcome:
            return Outcome.PROCESSED

    s = GoodS()
    assert isinstance(s, Sentinel)


def test_sentinel_default_should_process_returns_true():
    """Default should_process() hook must return True."""

    class MinS(Sentinel):
        name = "min"
        version = "1.0.0"
        event_source_kind = "jmap_poll"

        def process(self, event: Event, ctx: Context) -> Outcome:
            return Outcome.IGNORED

    sentinel = MinS()
    # Construct a minimal Event + Context to satisfy the signature
    event: Event = {
        "source_kind": "jmap_poll",
        "source_ref": "acct-1",
        "message_id": "msg-1",
        "payload": {},
    }
    ctx = Context(
        db_pool=None,
        mission_emitter=None,
        delegation=None,
        sentinel_row=None,
        logger=logging.getLogger("test"),
    )
    assert sentinel.should_process(event, ctx) is True


def test_sentinel_default_config_schema_returns_empty_dict():
    class MinS(Sentinel):
        name = "min"
        version = "1.0.0"
        event_source_kind = "rabbitmq"

        def process(self, event: Event, ctx: Context) -> Outcome:
            return Outcome.IGNORED

    assert MinS().config_schema() == {}


# ---------------------------------------------------------------------------
# Context dataclass
# ---------------------------------------------------------------------------


def test_context_has_expected_fields():
    """Context exposes the five spec fields plus SP5c 5.2's mutable trace."""
    fields = {f.name for f in dataclasses.fields(Context)}
    assert fields == {
        "db_pool",
        "mission_emitter",
        "delegation",
        "sentinel_row",
        "logger",
        "trace",  # SP5c 5.2: decision trace accumulator
    }


def test_context_is_mutable_dataclass():
    """Context is NOT frozen — the runtime may mutate fields post-construction."""
    ctx = Context(
        db_pool="pool",
        mission_emitter="emitter",
        delegation="deleg",
        sentinel_row="row",
        logger=logging.getLogger("x"),
    )
    ctx.db_pool = "new_pool"
    assert ctx.db_pool == "new_pool"


# ---------------------------------------------------------------------------
# Event TypedDict
# ---------------------------------------------------------------------------


def test_event_typeddict_happy_path():
    """Event TypedDict assignment must not raise at runtime."""
    e: Event = {
        "source_kind": "rabbitmq",
        "source_ref": "mail:message:received",
        "message_id": "abc-123",
        "payload": {"subject": "hello", "from": "sender@example.com"},
    }
    assert e["source_kind"] == "rabbitmq"
    assert e["message_id"] == "abc-123"
    assert e["payload"]["subject"] == "hello"


def test_event_jmap_poll_kind():
    e: Event = {
        "source_kind": "jmap_poll",
        "source_ref": "acct-xyz",
        "message_id": "email-id-42",
        "payload": {},
    }
    assert e["source_kind"] == "jmap_poll"
