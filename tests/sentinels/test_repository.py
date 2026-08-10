"""Integration tests for twaky.sentinels.repository.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_TEST_DSN env to override the default pg_dsn.

Seed row: 'mail' sentinel inserted by sql/008_init_sentinels.sh. Tests that
mutate config_values or sentinel state must restore them in a finally block
so the seed row stays intact for subsequent tests.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels import repository as repo
from twaky.sentinels.repository import SentinelNotFound


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
def _clean_runs():
    """Wipe sentinel_run before and after every test. Preserves seed rows."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sentinel_run")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sentinel_run")


# ---------------------------------------------------------------------------
# Seed sanity
# ---------------------------------------------------------------------------


def test_get_mail_seed_row():
    """get('mail') must return the seed row provisioned by 008_init_sentinels.sh."""
    row = repo.get("mail")
    assert row is not None
    assert row.name == "mail"
    assert row.version == "1.0.0"
    assert row.enabled is True
    assert row.config_values["event_source"] == "jmap_poll"
    assert row.config_values["pattern_confidence_threshold"] == 0.9


def test_get_unknown_sentinel_returns_none():
    assert repo.get("nonexistent-sentinel-xyz") is None


# ---------------------------------------------------------------------------
# list_all / list_enabled
# ---------------------------------------------------------------------------


def test_list_all_includes_mail():
    names = [s.name for s in repo.list_all()]
    assert "mail" in names


def test_list_enabled_excludes_disabled_sentinel():
    try:
        repo.update("mail", {"enabled": False})
        enabled_names = [s.name for s in repo.list_enabled()]
        assert "mail" not in enabled_names
        # list_all still includes it
        all_names = [s.name for s in repo.list_all()]
        assert "mail" in all_names
    finally:
        repo.update("mail", {"enabled": True})


# ---------------------------------------------------------------------------
# update() validation
# ---------------------------------------------------------------------------


def test_update_empty_patch_raises_value_error():
    with pytest.raises(ValueError, match="empty patch"):
        repo.update("mail", {})


def test_update_unknown_field_raises_value_error():
    with pytest.raises(ValueError, match="unknown fields"):
        repo.update("mail", {"nonexistent_key": "boom"})


def test_update_missing_sentinel_raises_not_found():
    with pytest.raises(SentinelNotFound):
        repo.update("this-sentinel-does-not-exist", {"enabled": True})


def test_update_enabled_toggles_correctly():
    try:
        updated = repo.update("mail", {"enabled": False})
        assert updated.enabled is False
    finally:
        repo.update("mail", {"enabled": True})
    # Verify restored
    assert repo.get("mail").enabled is True


# ---------------------------------------------------------------------------
# update_config_value — JMAP adapter regression
# ---------------------------------------------------------------------------


def test_update_config_value_merges_without_clobbering_siblings():
    """Writing jmap_last_state must NOT clobber event_source or
    pattern_confidence_threshold (regression: dict param binding for JMAP adapter)."""
    try:
        updated = repo.update_config_value("mail", "jmap_last_state", "state-abc")
        cv = updated.config_values
        # New key written
        assert cv["jmap_last_state"] == "state-abc"
        # Siblings preserved
        assert cv["event_source"] == "jmap_poll"
        assert cv["pattern_confidence_threshold"] == 0.9
        assert cv["memory_candidate_pool"] == 100
    finally:
        # Remove the injected key by rebuilding config_values without it
        current = repo.get("mail")
        cleaned = {
            k: v for k, v in current.config_values.items() if k != "jmap_last_state"
        }
        repo.update("mail", {"config_values": cleaned})


def test_update_config_value_missing_sentinel_raises_not_found():
    with pytest.raises(SentinelNotFound):
        repo.update_config_value("ghost-sentinel", "key", "val")


# ---------------------------------------------------------------------------
# sentinel_run CRUD
# ---------------------------------------------------------------------------


def _make_run(**overrides) -> dict:
    base = {
        "sentinel_name": "mail",
        "event_ref": f"jmap_poll:acct-1:email-{uuid4()}",
        "outcome": "processed",
        "llm_calls": 2,
        "trace": [{"node": "match_rules", "result": "hit"}],
    }
    base.update(overrides)
    return base


def test_insert_and_get_run():
    inserted = repo.insert_run(_make_run())
    assert inserted.id is not None
    assert inserted.sentinel_name == "mail"
    assert inserted.outcome == "processed"
    assert inserted.llm_calls == 2

    fetched = repo.get_run(inserted.id)
    assert fetched is not None
    assert fetched.id == inserted.id
    assert fetched.trace == [{"node": "match_rules", "result": "hit"}]


def test_get_run_unknown_id_returns_none():
    assert repo.get_run(uuid4()) is None


def test_update_run():
    run = repo.insert_run(_make_run(outcome="processed"))
    completed = datetime.now(tz=UTC)
    updated = repo.update_run(
        run.id,
        {
            "outcome": "mission_created",
            "completed_at": completed,
            "duration_ms": 1234,
            "llm_calls": 5,
        },
    )
    assert updated.outcome == "mission_created"
    assert updated.duration_ms == 1234
    assert updated.llm_calls == 5


def test_update_run_empty_patch_raises_value_error():
    run = repo.insert_run(_make_run())
    with pytest.raises(ValueError, match="empty patch"):
        repo.update_run(run.id, {})


def test_update_run_unknown_field_raises_value_error():
    run = repo.insert_run(_make_run())
    with pytest.raises(ValueError, match="unknown fields"):
        repo.update_run(run.id, {"bad_field": "x"})


def test_list_runs_orders_by_started_at_desc():
    """list_runs must return rows newest-first."""
    now = datetime.now(tz=UTC)
    older = now - timedelta(hours=2)
    newer = now - timedelta(seconds=30)

    repo.insert_run(_make_run(started_at=older, event_ref="jmap_poll:a:old"))
    repo.insert_run(_make_run(started_at=newer, event_ref="jmap_poll:a:new"))

    runs = repo.list_runs("mail")
    assert len(runs) >= 2
    # Verify descending order
    for i in range(len(runs) - 1):
        assert runs[i].started_at >= runs[i + 1].started_at


def test_list_runs_limit():
    for _ in range(5):
        repo.insert_run(_make_run())
    runs = repo.list_runs("mail", limit=3)
    assert len(runs) == 3


# ---------------------------------------------------------------------------
# find_run_by_event_ref
# ---------------------------------------------------------------------------


def test_find_run_by_event_ref_within_24h():
    event_ref = f"jmap_poll:acct-1:email-{uuid4()}"
    inserted = repo.insert_run(_make_run(event_ref=event_ref))
    found = repo.find_run_by_event_ref("mail", event_ref)
    assert found is not None
    assert found.id == inserted.id


def test_find_run_by_event_ref_outside_24h_returns_none():
    """A run started 48 h ago must NOT be found with the default 24-h window."""
    event_ref = f"jmap_poll:acct-1:email-{uuid4()}"
    started_48h_ago = datetime.now(tz=UTC) - timedelta(hours=48)
    repo.insert_run(_make_run(event_ref=event_ref, started_at=started_48h_ago))
    found = repo.find_run_by_event_ref("mail", event_ref, within_hours=24)
    assert found is None


def test_find_run_by_event_ref_72h_override_finds_old_row():
    """Extending the window to 72 h must find a row that is 48 h old."""
    event_ref = f"jmap_poll:acct-1:email-{uuid4()}"
    started_48h_ago = datetime.now(tz=UTC) - timedelta(hours=48)
    inserted = repo.insert_run(
        _make_run(event_ref=event_ref, started_at=started_48h_ago)
    )
    found = repo.find_run_by_event_ref("mail", event_ref, within_hours=72)
    assert found is not None
    assert found.id == inserted.id


def test_find_run_by_event_ref_no_match_returns_none():
    result = repo.find_run_by_event_ref("mail", "jmap_poll:acct-1:no-such-email")
    assert result is None


# ---------------------------------------------------------------------------
# count_runs_24h
# ---------------------------------------------------------------------------


def test_count_runs_24h_total_and_errors():
    """count_runs_24h must split runs correctly by outcome."""
    repo.insert_run(_make_run(outcome="processed"))
    repo.insert_run(_make_run(outcome="mission_created"))
    repo.insert_run(_make_run(outcome="error"))
    repo.insert_run(_make_run(outcome="error"))

    total, errors = repo.count_runs_24h("mail")
    assert total == 4
    assert errors == 2


def test_count_runs_24h_no_errors():
    repo.insert_run(_make_run(outcome="processed"))
    total, errors = repo.count_runs_24h("mail")
    assert total == 1
    assert errors == 0


def test_count_runs_24h_excludes_old_runs():
    """Runs older than 24 h must not be counted."""
    old_start = datetime.now(tz=UTC) - timedelta(hours=25)
    repo.insert_run(_make_run(outcome="error", started_at=old_start))
    total, errors = repo.count_runs_24h("mail")
    assert total == 0
    assert errors == 0


# ---------------------------------------------------------------------------
# purge_old_runs
# ---------------------------------------------------------------------------


def test_purge_old_runs_returns_deleted_count():
    """purge_old_runs(30) must delete rows older than 30 days and return count."""
    old_start = datetime.now(tz=UTC) - timedelta(days=31)
    repo.insert_run(_make_run(started_at=old_start))
    repo.insert_run(_make_run(started_at=old_start))
    # Recent run — must survive
    repo.insert_run(_make_run())

    deleted = repo.purge_old_runs(30)
    assert deleted == 2

    remaining = repo.list_runs("mail", limit=100)
    assert len(remaining) == 1


def test_purge_old_runs_nothing_to_purge_returns_zero():
    repo.insert_run(_make_run())  # recent, should survive
    deleted = repo.purge_old_runs(30)
    assert deleted == 0
