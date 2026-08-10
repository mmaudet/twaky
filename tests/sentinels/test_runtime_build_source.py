"""Regression tests for SentinelRuntime._build_source.

These tests ensure that _build_source correctly instantiates the right
EventSource subclass without raising TypeError — the kind of silent breakage
that occurred when JmapPollingEventSource dropped the bearer_token kwarg in T9
but _build_source was not updated (SP6b final-review item C1).

No DB or RabbitMQ connection is needed; all dependencies are faked.
"""

from __future__ import annotations

from typing import Literal
from unittest.mock import MagicMock, patch

from twaky.config import Settings
from twaky.sentinels.base import Context, Event, Outcome, Sentinel
from twaky.sentinels.models import SentinelConfig
from twaky.sentinels.runtime import _build_source
from twaky.sentinels.sources.jmap_poll import JmapPollingEventSource
from twaky.sentinels.sources.rabbitmq import RabbitMQEventSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_sentinel(name: str, kind: Literal["jmap_poll", "rabbitmq"]) -> Sentinel:
    """Return a minimal Sentinel-like object with .name and .event_source_kind."""

    class _FakeSentinel(Sentinel):
        event_source_kind: Literal["rabbitmq", "jmap_poll"] = kind  # type: ignore[assignment]

        def process(self, event: Event, ctx: Context) -> Outcome:  # pragma: no cover
            return Outcome.PROCESSED

    inst = _FakeSentinel.__new__(_FakeSentinel)
    inst.__dict__["name"] = name
    inst.__dict__["event_source_kind"] = kind
    return inst


def _fake_sentinel_row(config_values: dict | None = None) -> SentinelConfig:
    """Return a minimal SentinelConfig with empty config_values."""
    row = SentinelConfig.__new__(SentinelConfig)
    row.__dict__.update(
        {
            "name": "mail",
            "version": "0.0.1",
            "enabled": True,
            "config_schema": {},
            "config_values": config_values or {},
            "created_at": None,
            "updated_at": None,
        }
    )
    return row


def _fake_context(row: SentinelConfig) -> Context:
    return Context(
        db_pool=None,
        mission_emitter=MagicMock(),
        delegation=MagicMock(),
        sentinel_row=row,
        logger=__import__("logging").getLogger("test.build_source"),
    )


def _jmap_settings() -> Settings:
    """Return a minimal Settings instance sufficient for jmap_poll build."""
    return Settings(  # type: ignore[call-arg]
        twaky_owner_email="owner@example.com",
        twaky_pg_host="localhost",
        twaky_pg_port=5432,
        twaky_pg_db="twaky",
        twaky_pg_user="twaky",
        twaky_pg_password="pw",
        rabbitmq_url="amqp://guest:guest@localhost:5672/%2F",
        jmap_session_url="https://x.example.com/jmap/session",
        jmap_bearer_token="legacy-unused-token",
        jmap_account_email="user@example.com",
        jmap_poll_interval_s=60,
    )


def _rabbitmq_settings() -> Settings:
    return _jmap_settings()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_source_jmap_poll_constructs_correctly():
    """_build_source with kind=jmap_poll must return JmapPollingEventSource.

    Regression guard for C1: bearer_token was a dead kwarg after T9 refactor.
    If _build_source still passes it, JmapPollingEventSource raises TypeError
    which gets swallowed by the bare except in _run_one, silently killing the
    mail sentinel.
    """
    settings = _jmap_settings()
    inst = _fake_sentinel("mail", "jmap_poll")
    row = _fake_sentinel_row()
    ctx = _fake_context(row)

    # get_manager is called inside JmapPollingEventSource.__init__ when
    # refresh_manager is None; patch it to avoid DB/oauth lookup.
    with patch(
        "twaky.sentinels.sources.jmap_poll.get_manager", return_value=MagicMock()
    ):
        source = _build_source(inst, ctx, settings)

    assert isinstance(source, JmapPollingEventSource)


def test_build_source_rabbitmq_constructs_correctly():
    """_build_source with kind=rabbitmq must return RabbitMQEventSource.

    Symmetry guard: protects against similar future refactor mistakes in the
    RabbitMQ branch.
    """
    settings = _rabbitmq_settings()
    inst = _fake_sentinel("mail", "rabbitmq")
    row = _fake_sentinel_row(config_values={"bindings": []})
    ctx = _fake_context(row)

    source = _build_source(inst, ctx, settings)

    assert isinstance(source, RabbitMQEventSource)
