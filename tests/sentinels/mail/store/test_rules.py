"""Tests for twaky.sentinels.mail.store.rules.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 env to override default host.

Validation tests (TestValidation) are pure unit tests but share the
file-level pytestmark for consistency.
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.store import rules as store
from twaky.sentinels.mail.store.rules import RuleValidationError


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe():
    """Delete all rows from mail_sentinel_rule before and after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_rule")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_rule")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_rule(**overrides):
    """Return kwargs for store.create() with sensible defaults."""
    base: dict = {
        "name": f"test-rule-{uuid4().hex[:8]}",
        "conditions": [
            {"field": "from", "operator": "contains", "value": "example.com"}
        ],
        "combinator": "OR",
        "actions": ["archive"],
        "priority": 100,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestValidation — pure unit, no DB
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_conditions(self):
        conds = [
            {"field": "from", "operator": "contains", "value": "newsletter@"},
            {
                "field": "header:List-Id",
                "operator": "equals",
                "value": "<example.list>",
            },
        ]
        # Should not raise
        store.validate_conditions(conds)

    def test_bad_field(self):
        with pytest.raises(RuleValidationError, match="unknown field"):
            store.validate_conditions(
                [{"field": "bogus", "operator": "contains", "value": "x"}]
            )

    def test_bad_operator(self):
        with pytest.raises(RuleValidationError, match="unknown operator"):
            store.validate_conditions(
                [{"field": "from", "operator": "startswith", "value": "x"}]
            )

    def test_bad_regex(self):
        with pytest.raises(RuleValidationError, match="does not compile"):
            store.validate_conditions(
                [{"field": "subject", "operator": "regex", "value": "[unclosed"}]
            )

    def test_action_label_form(self):
        # Should not raise
        store.validate_actions(["label:invoice"])

    def test_action_bad(self):
        with pytest.raises(RuleValidationError, match="unknown action"):
            store.validate_actions(["ignore"])

    def test_name_regex_valid(self):
        # Should not raise
        store.validate_name("archive-newsletters")

    def test_name_regex_uppercase_raises(self):
        with pytest.raises(RuleValidationError, match="invalid rule name"):
            store.validate_name("Archive")

    def test_name_regex_leading_digit_raises(self):
        with pytest.raises(RuleValidationError, match="invalid rule name"):
            store.validate_name("1st-rule")


# ---------------------------------------------------------------------------
# TestCRUD — integration
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_create_read(self):
        rule = store.create(
            **_make_rule(
                name="test-create-read",
                actions=["mark_read", "archive"],
            )
        )
        assert rule.id is not None
        fetched = store.by_name("test-create-read")
        assert fetched is not None
        assert fetched.id == rule.id
        assert fetched.actions == ["mark_read", "archive"]

    def test_list_all_orders_by_priority_then_name(self):
        store.create(**_make_rule(name="beta-rule", priority=50))
        store.create(**_make_rule(name="alpha-rule", priority=100))
        store.create(**_make_rule(name="gamma-rule", priority=50))

        rules = store.list_all()
        assert len(rules) == 3

        # priority 50 first (two of them), then priority 100
        assert rules[0].priority == 50
        assert rules[1].priority == 50
        assert rules[2].priority == 100

        # within the same priority, name ASC
        same_prio = [r for r in rules if r.priority == 50]
        names = [r.name for r in same_prio]
        assert names == sorted(names)

    def test_list_enabled_only(self):
        store.create(**_make_rule(name="enabled-rule", enabled=True))
        store.create(**_make_rule(name="disabled-rule", enabled=False))

        all_rules = store.list_all()
        assert len(all_rules) == 2

        enabled_rules = store.list_all(enabled_only=True)
        assert len(enabled_rules) == 1
        assert enabled_rules[0].name == "enabled-rule"

    def test_update_patches_and_touches_updated_at(self):
        rule = store.create(**_make_rule(name="update-test-rule", priority=100))
        original_updated_at = rule.updated_at

        # Small sleep to ensure clock advances
        time.sleep(0.05)

        updated = store.update(rule.id, {"priority": 10})
        assert updated.priority == 10
        assert updated.updated_at > original_updated_at

    def test_delete(self):
        rule = store.create(**_make_rule(name="delete-test-rule"))
        assert store.get(rule.id) is not None

        store.delete(rule.id)
        assert store.get(rule.id) is None
