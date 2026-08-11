"""API tests for GET /mail-sentinel/runs.

Live-DB integration: requires TWAKY_PG_HOST=172.27.0.33 like the other
mail-sentinel API tests. Seeds sentinel_run rows via the sentinel
repository and (optionally) linked spam_decision rows via the store, then
verifies the endpoint joins them correctly.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.config import settings
from twaky.sentinels import repository as sentinel_repo
from twaky.sentinels.mail.store import spam_decisions

_FERNET_KEY = Fernet.generate_key().decode()


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


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")
    monkeypatch.setenv("TWAKY_SECRET_KEY", _FERNET_KEY)
    from twaky import config as _cfg
    from twaky.crypto import secrets as _crypto

    cfg = _cfg.Settings(_env_file=None)
    monkeypatch.setattr("twaky.api.deps.settings", cfg)
    monkeypatch.setattr("twaky.crypto.secrets.settings", cfg)
    _crypto._fernet.cache_clear()
    yield
    _crypto._fernet.cache_clear()


@pytest.fixture(autouse=True)
def _wipe():
    """Wipe sentinel_run + spam_decision rows before and after each test.

    Guarded by TWAKY_ALLOW_DESTRUCTIVE_TESTS — see
    ``docs/superpowers/investigations/2026-08-12-spam-decision-purge.md``.
    """
    from tests._conftest_helpers import destructive_wipe_allowed, skip_reason

    if not destructive_wipe_allowed():
        pytest.skip(skip_reason())
    _truncate()
    yield
    _truncate()


def _truncate() -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision;")
        cur.execute("DELETE FROM sentinel_run WHERE sentinel_name = 'mail';")


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _owner_client(monkeypatch) -> TestClient:
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
    return TestClient(app, cookies=_cookie())


def _unauthenticated_client() -> TestClient:
    return TestClient(app)


def _seed_run(*, email_id: str, outcome: str = "processed") -> str:
    """Insert a sentinel_run row and return its id."""
    run = sentinel_repo.insert_run(
        {
            "sentinel_name": "mail",
            "event_ref": email_id,
            "outcome": outcome,
        }
    )
    return str(run.id)


def _seed_decision(
    *, email_id: str, bucket: str = "newsletter", signal: str = "heuristic_newsletter"
) -> None:
    spam_decisions.insert(
        email_id=email_id,
        thread_id=None,
        sender_email="news@x",
        subject="digest",
        received_at=datetime.now(UTC) - timedelta(minutes=5),
        bucket=bucket,
        signal_source=signal,
        score=None,
        reason=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_401_when_unauthenticated():
    r = _unauthenticated_client().get("/mail-sentinel/runs")
    assert r.status_code == 401


def test_returns_empty_when_no_runs(monkeypatch):
    r = _owner_client(monkeypatch).get("/mail-sentinel/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_returns_runs_with_spam_decision_joined(monkeypatch):
    """A run with a matching spam_decision surfaces bucket + signal_source."""
    email_a = f"Ea{uuid4().hex}"
    email_b = f"Eb{uuid4().hex}"
    _seed_run(email_id=email_a)  # will link to newsletter decision
    _seed_run(email_id=email_b)  # no decision — bucket must be null
    _seed_decision(email_id=email_a, bucket="newsletter")

    r = _owner_client(monkeypatch).get("/mail-sentinel/runs")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2

    by_email = {row["email_id"]: row for row in rows}
    a = by_email[email_a]
    assert a["spam_bucket"] == "newsletter"
    assert a["spam_signal_source"] == "heuristic_newsletter"
    assert a["spam_decision_id"] is not None
    assert a["outcome"] == "processed"

    b = by_email[email_b]
    assert b["spam_bucket"] is None
    assert b["spam_signal_source"] is None
    assert b["spam_decision_id"] is None


def test_limit_parameter_respected(monkeypatch):
    for i in range(5):
        _seed_run(email_id=f"E{i}{uuid4().hex}")
    r = _owner_client(monkeypatch).get("/mail-sentinel/runs?limit=3")
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_returns_error_run_with_null_completed(monkeypatch):
    """Runs that ended in error surface outcome=error + completed_at null."""
    email_id = f"Eerr{uuid4().hex}"
    _seed_run(email_id=email_id, outcome="error")
    r = _owner_client(monkeypatch).get("/mail-sentinel/runs")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["completed_at"] is None
