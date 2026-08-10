"""Live integration test: full OAuth refresh loop against LemonLDAP-NG + James.

Opt-in via EVAL_LIVE=1 + JMAP_OAUTH_CLIENT_SECRET + JMAP_TEST_REFRESH_TOKEN.
Skipped by default in CI (and in any environment without real credentials).

To run manually on twake-dev (after the SP6b UI connect flow has been
completed at least once to capture a refresh_token):

    EVAL_LIVE=1 \\
    TWAKY_PG_HOST=172.27.0.33 \\
    JMAP_TEST_REFRESH_TOKEN=<refresh_token from DevTools or DB> \\
    uv run pytest tests/integration/test_jmap_refresh_live.py -v

The test seeds an ``oauth_credential`` row with the supplied refresh_token,
calls ``RefreshManager.force_refresh()`` to obtain a fresh access_token, then
probes the JMAP session endpoint to confirm LemonLDAP-NG accepted the token.
Cleanup: the seeded row is deleted so the real credential (if any) is restored
from the previous state by the operator if needed.
"""

from __future__ import annotations

import os

import httpx
import pytest

from twaky.config import settings
from twaky.crypto.secrets import encrypt
from twaky.oauth import repository
from twaky.oauth.refresh_manager import RefreshManager

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

_EVAL_LIVE = os.environ.get("EVAL_LIVE", "0") == "1"
_REFRESH_TOKEN = os.environ.get("JMAP_TEST_REFRESH_TOKEN", "")

_SKIP_REASON = (
    "Live JMAP refresh test disabled — set EVAL_LIVE=1, "
    "JMAP_TEST_REFRESH_TOKEN, and JMAP_OAUTH_CLIENT_SECRET to enable"
)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not (_EVAL_LIVE and _REFRESH_TOKEN and settings.jmap_oauth_client_secret),
    reason=_SKIP_REASON,
)
async def test_full_refresh_loop_against_lemonldap() -> None:
    """Seed a credential, force-refresh, probe JMAP session → assert 200.

    Steps:
    1. Derive token_endpoint from ``settings.jmap_oauth_issuer`` per
       the OpenID Connect discovery convention ({issuer}/oauth2/token).
    2. Upsert an ``oauth_credential`` row for sentinel_name="mail" with the
       encrypted refresh_token from ``JMAP_TEST_REFRESH_TOKEN``.
    3. Instantiate ``RefreshManager("mail")`` and call ``force_refresh()``.
    4. Assert the returned access_token is a non-empty string.
    5. GET ``settings.jmap_session_url`` with the access_token as Bearer →
       assert HTTP 200 (proves LemonLDAP-NG issued a token James accepts).
    6. Cleanup: DELETE the ``oauth_credential`` row so the DB is left clean.
    """
    issuer = settings.jmap_oauth_issuer.rstrip("/")
    token_endpoint = f"{issuer}/oauth2/token"

    # ── 1. Seed credential row ─────────────────────────────────────────────
    repository.upsert(
        sentinel_name="mail",
        provider="lemonldap",
        client_id=settings.jmap_oauth_client_id,
        token_endpoint=token_endpoint,
        session_url=settings.jmap_session_url,
        scope=settings.jmap_oauth_scope,
        refresh_token_enc=encrypt(_REFRESH_TOKEN),
        access_token_enc=None,
        access_token_expires_at=None,
        account_email=None,
        account_name=None,
    )

    try:
        # ── 2. Force refresh ───────────────────────────────────────────────
        manager = RefreshManager("mail")
        access_token = await manager.force_refresh()

        assert access_token, "force_refresh() must return a non-empty access_token"
        assert isinstance(access_token, str), "access_token must be a str"

        # ── 3. Probe JMAP session endpoint ─────────────────────────────────
        # A 200 response confirms that LemonLDAP-NG accepted our refresh_token
        # and that the resulting access_token is also accepted by James.
        assert settings.jmap_session_url, (
            "JMAP_SESSION_URL must be set for the live probe"
        )
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        ) as client:
            resp = await client.get(settings.jmap_session_url)

        assert resp.status_code == 200, (
            f"JMAP session probe returned {resp.status_code}: {resp.text[:200]}"
        )

    finally:
        # ── 4. Cleanup ─────────────────────────────────────────────────────
        repository.delete("mail")
