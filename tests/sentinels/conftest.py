"""Session-level guard: fail hard if integration tests are about to hit
a Postgres that looks like production.

Motivation (2026-08-14): a dev-side ``/etc/hosts`` shortcut pointed the
host Python venv at the live ``twaky-pg`` container. Running
``pytest tests/sentinels/`` from the host then wiped every
``mail_sentinel_*`` table (learned_patterns, memories, observations) via
per-test ``DELETE FROM …`` fixtures. Real learned data lost.

Guard rule: integration tests may only run when either
- ``TWAKY_TEST_DSN`` env var is set (points at a dedicated test DB), OR
- ``TWAKY_ALLOW_PROD_TESTS=1`` is set (explicit escape hatch for CI
  environments where the DSN happens to be prod-shaped but writes are
  isolated by other means).

Otherwise the session stops before any test runs.
"""

from __future__ import annotations

import os
import re

import pytest

_REFUSAL = """\

❌ REFUSING to run integration tests against prod-shaped DSN.
   pg_dsn resolves to: {dsn}

   Set TWAKY_TEST_DSN=postgresql://... to point at a test DB, or
   TWAKY_ALLOW_PROD_TESTS=1 to explicitly opt in.

   Context: on 2026-08-14 the mail_sentinel_* tables were wiped by
   running these tests against the live twaky-pg — this guard exists
   so it never happens again.
"""


def _redact(dsn: str) -> str:
    """Mask the password in a libpq URI before it reaches stdout / CI logs."""
    return re.sub(r"(://[^:/@]+:)[^@]*(@)", r"\1***\2", dsn)


def pytest_configure(config):
    """Stop the session early if running against what looks like the prod twaky-pg.

    Uses ``pytest.exit`` rather than ``SystemExit``: raising SystemExit from a
    ``pytest_configure`` hook escapes through pluggy and surfaces as a 40-line
    INTERNALERROR traceback that buries the message above. ``pytest.exit``
    unwinds through pytest's own session handling, so the refusal is the last
    thing on screen.
    """
    if os.environ.get("TWAKY_ALLOW_PROD_TESTS") == "1":
        return
    if os.environ.get("TWAKY_TEST_DSN"):
        return

    # Prod-shape heuristic: settings.pg_dsn resolves to twaky-pg (docker
    # network hostname) or contains the twaky-pg host in /etc/hosts.
    try:
        from twaky.config import settings

        dsn = settings.pg_dsn
    except Exception:  # noqa: BLE001 - a guard that crashes is worse than one
        # that lets tests through: any failure to resolve the DSN (missing
        # required env var, import error) means we cannot prove the target is
        # prod, so we stay out of the way.
        return

    if "twaky-pg" in dsn:
        # Only block if we can actually reach it — otherwise the DSN is
        # meaningless (CI without a twaky-pg host).
        import socket

        try:
            socket.gethostbyname("twaky-pg")
        except OSError:
            return  # host doesn't resolve, safe

        pytest.exit(_REFUSAL.format(dsn=_redact(dsn)), returncode=2)
