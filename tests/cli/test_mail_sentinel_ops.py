"""Unit tests for the twaky mail-sentinel operational sub-commands.

Covers ``rules list / rules toggle`` and ``decisions list / decisions stats``.
Each command is invoked via Typer's ``CliRunner`` with the store layer
patched to return canned data — the tests never touch a real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from typer.testing import CliRunner

from twaky.cli.mail_sentinel import app as mail_sentinel_app  # type: ignore[import-untyped]


runner = CliRunner()


# ---------------------------------------------------------------------------
# rules list
# ---------------------------------------------------------------------------


def _fake_rule(
    *,
    name: str,
    priority: int = 100,
    enabled: bool = True,
    actions: list[str] | None = None,
) -> MagicMock:
    r = MagicMock()
    r.id = uuid4()
    r.name = name
    r.priority = priority
    r.enabled = enabled
    r.actions = actions or ["label:test"]
    return r


def test_rules_list_orders_by_priority() -> None:
    rules = [
        _fake_rule(name="c_rule", priority=100),
        _fake_rule(name="a_rule", priority=40),
        _fake_rule(name="b_rule", priority=90, enabled=False),
    ]
    with patch("twaky.sentinels.mail.store.rules.list_all", return_value=rules):
        result = runner.invoke(mail_sentinel_app, ["rules", "list"])
    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    # First data row after the header must be the priority=40 rule
    assert "a_rule" in lines[1]
    # Disabled rule shown with "no" flag
    assert "no" in [ln for ln in lines if "b_rule" in ln][0]


def test_rules_list_enabled_only_flag() -> None:
    rules = [_fake_rule(name="only_one", priority=50)]
    with patch(
        "twaky.sentinels.mail.store.rules.list_all", return_value=rules
    ) as mock_list:
        result = runner.invoke(mail_sentinel_app, ["rules", "list", "--enabled-only"])
    assert result.exit_code == 0
    mock_list.assert_called_once_with(enabled_only=True)


def test_rules_list_empty_prints_placeholder() -> None:
    with patch("twaky.sentinels.mail.store.rules.list_all", return_value=[]):
        result = runner.invoke(mail_sentinel_app, ["rules", "list"])
    assert result.exit_code == 0
    assert "(no rules)" in result.stdout


# ---------------------------------------------------------------------------
# rules toggle
# ---------------------------------------------------------------------------


def test_rules_toggle_flips_and_reports_new_state() -> None:
    r = _fake_rule(name="ventes", enabled=True)
    r_after = _fake_rule(name="ventes", enabled=False)
    with (
        patch("twaky.sentinels.mail.store.rules.by_name", return_value=r),
        patch(
            "twaky.sentinels.mail.store.rules.update", return_value=r_after
        ) as mock_update,
    ):
        result = runner.invoke(mail_sentinel_app, ["rules", "toggle", "ventes"])

    assert result.exit_code == 0
    mock_update.assert_called_once_with(r.id, {"enabled": False})
    assert "'ventes' → disabled" in result.stdout


def test_rules_toggle_unknown_rule_exits_1() -> None:
    with patch("twaky.sentinels.mail.store.rules.by_name", return_value=None):
        result = runner.invoke(mail_sentinel_app, ["rules", "toggle", "nope"])
    assert result.exit_code == 1
    assert "'nope' not found" in result.stdout or "'nope' not found" in (
        result.stderr or ""
    )


# ---------------------------------------------------------------------------
# decisions list
# ---------------------------------------------------------------------------


def _fake_decision(
    *,
    bucket: str = "newsletter",
    sender: str = "news@x",
    subject: str = "Digest",
    restored: bool = False,
) -> MagicMock:
    d = MagicMock()
    d.decided_at = datetime(2026, 8, 12, 10, 30, 0, tzinfo=UTC)
    d.bucket = bucket
    d.signal_source = "heuristic_newsletter"
    d.sender_email = sender
    d.subject = subject
    d.restored_at = datetime(2026, 8, 12, 11, 0, 0, tzinfo=UTC) if restored else None
    return d


def test_decisions_list_shows_recent_rows() -> None:
    rows = [
        _fake_decision(sender="a@x", subject="First"),
        _fake_decision(sender="b@y", subject="Second", restored=True),
    ]
    with patch(
        "twaky.sentinels.mail.store.spam_decisions.list_recent",
        return_value=rows,
    ) as mock_list:
        result = runner.invoke(mail_sentinel_app, ["decisions", "list", "-n", "5"])
    assert result.exit_code == 0
    mock_list.assert_called_once_with(bucket=None, limit=5)
    assert "First" in result.stdout
    assert "Second" in result.stdout
    # restored row shows "yes"
    lines = result.stdout.splitlines()
    second = [ln for ln in lines if "b@y" in ln][0]
    assert " yes " in second


def test_decisions_list_bucket_filter_forwarded_to_store() -> None:
    with patch(
        "twaky.sentinels.mail.store.spam_decisions.list_recent", return_value=[]
    ) as mock_list:
        result = runner.invoke(
            mail_sentinel_app, ["decisions", "list", "--bucket", "spam"]
        )
    assert result.exit_code == 0
    mock_list.assert_called_once_with(bucket="spam", limit=20)


# ---------------------------------------------------------------------------
# decisions stats
# ---------------------------------------------------------------------------


def test_decisions_stats_renders_all_buckets() -> None:
    stats_data = {
        "spam": 3,
        "newsletter": 12,
        "phishing_alert": 1,
        "restored": 2,
        "total_processed": 16,
    }
    with patch(
        "twaky.sentinels.mail.store.spam_decisions.stats", return_value=stats_data
    ) as mock_stats:
        result = runner.invoke(mail_sentinel_app, ["decisions", "stats", "--days", "7"])
    assert result.exit_code == 0
    mock_stats.assert_called_once_with(days=7)
    for label in ("spam:", "newsletter:", "phishing-alert:", "restored:"):
        assert label in result.stdout
    assert "Last 7 days" in result.stdout
