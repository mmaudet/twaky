"""Tests for POST /mail-sentinel/rules/propose (SP6d).

All tests monkeypatch ``spam_decisions.list_recent`` and
``rules_store.list_all`` in the router module so they run without a live DB.
The _env fixture sets up the required env-vars; no Postgres connection is made.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.sentinels.mail.store.rules import MailRule
from twaky.sentinels.mail.store.spam_decisions import SpamDecision

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Set required env-vars and patch settings for every test."""
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _client() -> TestClient:
    return TestClient(app)


def _owner_client() -> TestClient:
    return TestClient(app, cookies=_cookie())


def _fake_decision(
    *,
    sender_email: str = "spammer@evil.example",
    subject: str = "Buy now!",
    bucket: str = "spam",
    envelope_headers: dict[str, Any] | None = None,
) -> SpamDecision:
    return SpamDecision(
        id=uuid4(),
        email_id=f"Mtest{uuid4().hex}",
        thread_id=None,
        sender_email=sender_email,
        subject=subject,
        received_at=datetime.now(UTC),
        bucket=bucket,
        signal_source="rspamd_junk_keyword",
        score=None,
        reason=None,
        restored_at=None,
        restored_by=None,
        decided_at=datetime.now(UTC),
        envelope_headers=envelope_headers,
    )


def _fake_rule(
    *,
    name: str = "test-rule",
    priority: int = 50,
    enabled: bool = True,
    conditions: list[dict[str, Any]] | None = None,
    actions: list[str] | None = None,
) -> MailRule:
    return MailRule(
        id=uuid4(),
        name=name,
        description="",
        conditions=conditions
        or [{"field": "from", "operator": "contains", "value": "@"}],
        combinator="OR",
        actions=actions or ["archive"],
        priority=priority,
        enabled=enabled,
        run_on_threads=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _propose_body(**over) -> dict:
    body: dict = {
        "name": "my-rule",
        "priority": 80,
        "enabled": True,
        "condition": {"from_contains": "acme.com"},
        "actions": ["archive"],
        "window": {"kind": "recent", "count": 200},
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestProposeUnauthenticated:
    def test_propose_unauthenticated_returns_401(self):
        r = _client().post("/mail-sentinel/rules/propose", json=_propose_body())
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestProposeValidation:
    def test_propose_invalid_rule_returns_422(self):
        """An invalid condition schema must return 422 with validation_failed."""
        # An empty condition dict has no keys → invalid
        body = _propose_body(condition={})
        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=[],
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_propose_count_over_2000_returns_422(self):
        """window.count > 2000 must return 422 with a clear message."""
        body = _propose_body(window={"kind": "recent", "count": 2001})
        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=[],
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)
        assert r.status_code == 422
        data = r.json()
        assert data["error"]["code"] == "validation_failed"
        assert "2000" in data["error"]["message"]

    def test_propose_invalid_action_returns_422(self):
        """Unknown action string must return 422 with validation_failed."""
        body = _propose_body(actions=["fly_to_moon"])
        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=[],
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_propose_invalid_header_matches_regex_returns_422(self):
        """Invalid regex in header_matches must return 422."""
        body = _propose_body(
            condition={
                "header_matches": {"name": "List-Unsubscribe", "regex": "[unclosed"}
            }
        )
        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=[],
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------


class TestProposeMatching:
    def test_propose_matches_expected_decisions(self):
        """Seed 3 fake decisions; propose a rule matching one; assert matched_count=1."""
        decisions = [
            _fake_decision(sender_email="user@acme.com", subject="Hello"),
            _fake_decision(sender_email="user@other.example", subject="Hi"),
            _fake_decision(sender_email="another@other.example", subject="Hey"),
        ]
        body = _propose_body(condition={"from_contains": "acme.com"})

        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=decisions,
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)

        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["matched_count"] == 1
        assert len(data["matched_examples"]) == 1
        assert data["matched_examples"][0]["sender"] == "user@acme.com"
        assert data["matched_examples"][0]["subject"] == "Hello"
        assert data["would_shadow_count"] == 0
        assert data["would_shadow"] == []

    def test_propose_reports_would_shadow(self):
        """Earlier-priority enabled rule that matches → would_shadow_count=1."""
        # A decision from acme.com
        decisions = [_fake_decision(sender_email="user@acme.com", subject="Invoice")]
        # An earlier-priority rule (priority 40 < proposed 80) that matches "from contains acme"
        earlier_rule = _fake_rule(
            name="newsletter-unsub",
            priority=40,
            enabled=True,
            conditions=[{"field": "from", "operator": "contains", "value": "acme.com"}],
        )

        body = _propose_body(
            condition={"from_contains": "acme.com"},
            priority=80,
        )

        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=decisions,
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[earlier_rule],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)

        assert r.status_code == 200
        data = r.json()
        assert data["matched_count"] == 1
        assert data["would_shadow_count"] == 1
        assert data["would_shadow"] == ["newsletter-unsub"]
        assert data["matched_examples"][0]["would_shadow_by"] == "newsletter-unsub"

    def test_propose_matched_examples_capped_at_10(self):
        """Seed 20 matching decisions; matched_count=20 but examples capped at 10."""
        decisions = [
            _fake_decision(sender_email="user@acme.com", subject=f"Email {i}")
            for i in range(20)
        ]
        body = _propose_body(condition={"from_contains": "acme.com"})

        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=decisions,
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)

        assert r.status_code == 200
        data = r.json()
        assert data["matched_count"] == 20
        assert len(data["matched_examples"]) == 10


# ---------------------------------------------------------------------------
# Simulation partial
# ---------------------------------------------------------------------------


class TestProposeSimulationPartial:
    def test_propose_simulation_partial_true_when_header_rule_and_null_headers(self):
        """header_matches + one decision with envelope_headers=None → partial=True."""
        decisions = [
            _fake_decision(
                sender_email="user@lists.example.com",
                envelope_headers={
                    "list-unsubscribe": "<https://lists.example.com/unsub>"
                },
            ),
            _fake_decision(
                sender_email="other@example.com",
                envelope_headers=None,  # pre-migration row
            ),
        ]
        body = _propose_body(
            condition={
                "header_matches": {
                    "name": "list-unsubscribe",
                    "regex": "https://",
                }
            }
        )

        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=decisions,
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)

        assert r.status_code == 200
        data = r.json()
        assert data["simulation_partial"] is True
        assert data["simulation_partial_reason"] is not None
        # Must mention the count of rows lacking headers
        assert "1" in data["simulation_partial_reason"]

    def test_propose_simulation_partial_false_when_no_header_rule(self):
        """Simple from_contains rule with all-null headers → partial=False."""
        decisions = [
            _fake_decision(sender_email="user@acme.com", envelope_headers=None),
            _fake_decision(sender_email="other@example.com", envelope_headers=None),
        ]
        # Only matching the from field, not any headers
        body = _propose_body(condition={"from_contains": "acme.com"})

        with (
            patch(
                "twaky.api.routers.mail_sentinel.spam_decisions.list_recent",
                return_value=decisions,
            ),
            patch(
                "twaky.api.routers.mail_sentinel.rules_store.list_all",
                return_value=[],
            ),
        ):
            r = _owner_client().post("/mail-sentinel/rules/propose", json=body)

        assert r.status_code == 200
        data = r.json()
        assert data["simulation_partial"] is False
        assert data["simulation_partial_reason"] is None
