"""Shared test helpers for destructive integration fixtures.

Sits outside a ``conftest.py`` on purpose — this module is imported
explicitly by tests that need the guard. Keeps behaviour opt-in per
test file rather than autoloading and surprising unrelated tests.

Rationale: on 2026-08-12 the ``_wipe()`` fixtures in
``tests/sentinels/mail/…`` and ``tests/api/routers/…`` were found to
DELETE from live production tables when developers ran the suite with
``TWAKY_PG_HOST=172.27.0.33`` (the shared dev DB). Six real
spam-decision rows disappeared silently. See
``docs/superpowers/investigations/2026-08-12-spam-decision-purge.md``.

The gate below refuses to run ``DELETE`` in ``_wipe()`` fixtures unless
the developer sets ``TWAKY_ALLOW_DESTRUCTIVE_TESTS=1`` — an explicit,
wilful opt-in.
"""

from __future__ import annotations

import os

_ENV_FLAG = "TWAKY_ALLOW_DESTRUCTIVE_TESTS"

_SKIP_REASON = (
    f"destructive integration test skipped — set {_ENV_FLAG}=1 to opt in. "
    f"See docs/superpowers/investigations/2026-08-12-spam-decision-purge.md"
)


def destructive_wipe_allowed() -> bool:
    """Return True when the caller has explicitly opted in to a destructive wipe.

    Any of ``1 / true / yes`` (case-insensitive) counts as allowed.
    """
    return os.environ.get(_ENV_FLAG, "").lower() in {"1", "true", "yes"}


def skip_reason() -> str:
    """Human-readable reason returned by pytest.skip when the gate is closed."""
    return _SKIP_REASON
