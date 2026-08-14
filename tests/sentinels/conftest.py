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

Otherwise the session errors out before any test collection completes.
"""

from __future__ import annotations

import os
import sys


def pytest_configure(config):
    """Fail early if running against what looks like the prod twaky-pg."""
    if os.environ.get("TWAKY_ALLOW_PROD_TESTS") == "1":
        return
    if os.environ.get("TWAKY_TEST_DSN"):
        return

    # Prod-shape heuristic: settings.pg_dsn resolves to twaky-pg (docker
    # network hostname) or contains the twaky-pg host in /etc/hosts.
    try:
        from twaky.config import settings

        dsn = settings.pg_dsn
    except Exception:
        return  # can't check → let tests proceed

    if "twaky-pg" in dsn or "@twaky-pg" in dsn:
        # Only block if we can actually reach it — otherwise the DSN is
        # meaningless (CI without a twaky-pg host).
        import socket

        try:
            socket.gethostbyname("twaky-pg")
        except OSError:
            return  # host doesn't resolve, safe

        print(
            "\n\n"
            "❌ REFUSING to run integration tests against prod-shaped DSN.\n"
            f"   pg_dsn resolves to: {dsn}\n"
            "\n"
            "   Set TWAKY_TEST_DSN=postgresql://... to point at a test DB, or\n"
            "   TWAKY_ALLOW_PROD_TESTS=1 to explicitly opt in.\n"
            "\n"
            "   Context: on 2026-08-14 the ``mail_sentinel_*`` tables were\n"
            "   wiped by running these tests against the live twaky-pg —\n"
            "   this guard exists so it never happens again.\n",
            file=sys.stderr,
        )
        raise SystemExit(2)
