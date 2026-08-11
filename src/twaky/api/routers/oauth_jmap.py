"""OIDC authorization code flow for the JMAP mail sentinel.

Implements the code flow against the owner's LemonLDAP-NG deployment:
GET /oauth/jmap/login redirects to the issuer's authorize endpoint
(discovered via .well-known/openid-configuration) with PKCE (S256) and
a signed state cookie carrying return_to. GET /oauth/jmap/callback
verifies the state cookie, exchanges the code for tokens via raw httpx
(POST token_endpoint with grant_type=authorization_code + code_verifier),
fetches userinfo, probes the JMAP session URL to confirm the token is
accepted, then upserts an oauth_credential row (SP6b T4) with Fernet-
encrypted access + refresh tokens (SP6b T3). Redirects back to return_to
with ?status=connected or ?status=error&reason=<code>.
"""

from __future__ import annotations

import json
import secrets
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlencode

import httpx
import itsdangerous
import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from twaky.api.deps import require_owner
from twaky.api.routers.oauth import _safe_return_to
from twaky.config import settings
from twaky.crypto.secrets import encrypt
from twaky.oauth import repository

router = APIRouter(prefix="/oauth/jmap", tags=["oauth-jmap"])

log = structlog.get_logger("twaky.api.oauth_jmap")

JMAP_STATE_COOKIE = "twaky_jmap_state"
STATE_TTL_SECONDS = 600  # 10 min

# Module-level cache: issuer URL → discovery dict.
_oidc_metadata_cache: dict[str, dict[str, str]] = {}


async def _get_oidc_metadata() -> dict[str, str]:
    """Fetch and cache .well-known/openid-configuration for the JMAP issuer.

    Returns a dict with at least ``authorization_endpoint``, ``token_endpoint``,
    and ``userinfo_endpoint``.  On discovery failure (network error, non-200
    response) falls back to LemonLDAP-NG conventional paths and logs a warning
    so the issue surfaces without breaking an MVP deployment.
    """
    issuer = settings.jmap_oauth_issuer.rstrip("/")
    if issuer in _oidc_metadata_cache:
        return _oidc_metadata_cache[issuer]

    discovery_url = issuer + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(discovery_url)
        if resp.status_code == 200:
            metadata = resp.json()
            _oidc_metadata_cache[issuer] = metadata
            return metadata
        log.warning(
            "oidc_discovery_non_200",
            url=discovery_url,
            status=resp.status_code,
        )
    except Exception as exc:
        log.warning("oidc_discovery_failed", url=discovery_url, exc_info=exc)

    # Fallback: LemonLDAP-NG conventional paths
    fallback: dict[str, str] = {
        "authorization_endpoint": issuer + "/oauth2/authorize",
        "token_endpoint": issuer + "/oauth2/token",
        "userinfo_endpoint": issuer + "/oauth2/userinfo",
    }
    return fallback


def _signer() -> itsdangerous.TimestampSigner:
    return itsdangerous.TimestampSigner(str(settings.api_session_secret))


def _make_state_cookie_value(payload: dict) -> str:
    """Sign and encode the state payload as a cookie value."""
    return _signer().sign(json.dumps(payload).encode()).decode()


def _verify_state_cookie(value: str) -> dict:
    """Unsign and decode a state cookie.  Raises SignatureExpired / BadSignature on failure."""
    raw = _signer().unsign(value, max_age=STATE_TTL_SECONDS)
    return json.loads(raw)


def _redirect_to(return_to: str, **params: str) -> RedirectResponse:
    """Build a 302 to return_to with query params appended safely."""
    safe = _safe_return_to(return_to)
    if "?" in safe:
        url = safe + "&" + urlencode(params)
    else:
        url = safe + "?" + urlencode(params)
    return RedirectResponse(url=url, status_code=302)


def _error_redirect(return_to: str, reason: str) -> RedirectResponse:
    """Build an error 302 and delete the JMAP state cookie.

    Clears the ``twaky_jmap_state`` cookie on all error paths to avoid
    leaving a stale signed state on the browser (minor CSRF-hygiene concern).
    """
    response = _redirect_to(return_to, status="error", reason=reason)
    response.delete_cookie(key=JMAP_STATE_COOKIE, path="/")
    return response


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge_s256)."""
    verifier = secrets.token_urlsafe(96)[:128]  # 128-char URL-safe string
    digest = sha256(verifier.encode("ascii")).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@router.get("/login")
async def jmap_login(
    request: Request,
    return_to: str = "/sentinels/mail?tab=auth",
    _email: str = Depends(require_owner),
) -> RedirectResponse:
    """Kick off the JMAP OAuth Authorization Code + PKCE flow."""
    safe_return_to = _safe_return_to(return_to)
    code_verifier, code_challenge = _pkce_pair()
    state_token = secrets.token_urlsafe(32)

    # Store return_to + PKCE verifier + state in a signed cookie.
    payload = {
        "return_to": safe_return_to,
        "code_verifier": code_verifier,
        "state": state_token,
    }
    cookie_value = _make_state_cookie_value(payload)

    callback_url = f"{settings.api_base_url.rstrip('/')}/oauth/jmap/callback"

    # Discover the authorize endpoint; fallback to conventional path on failure.
    metadata = await _get_oidc_metadata()
    authorize_url = metadata["authorization_endpoint"]
    params = {
        "response_type": "code",
        "client_id": settings.jmap_oauth_client_id,
        "redirect_uri": callback_url,
        "scope": settings.jmap_oauth_scope,
        "state": state_token,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    redirect_url = authorize_url + "?" + urlencode(params)

    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        key=JMAP_STATE_COOKIE,
        value=cookie_value,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/callback")
async def jmap_callback(
    request: Request,
    _email: str = Depends(require_owner),
) -> RedirectResponse:
    """Handle JMAP OAuth provider callback, exchange code, store credential."""
    default_return_to = "/sentinels/mail?tab=auth"

    # --- 1. Verify state cookie ---
    cookie_value = request.cookies.get(JMAP_STATE_COOKIE)
    if not cookie_value:
        log.warning("jmap_callback_no_state_cookie")
        return _error_redirect(default_return_to, "state_mismatch")

    try:
        state_payload = _verify_state_cookie(cookie_value)
    except itsdangerous.SignatureExpired:
        log.warning("jmap_callback_state_expired")
        return _error_redirect(default_return_to, "state_expired")
    except itsdangerous.BadSignature:
        log.warning("jmap_callback_bad_signature")
        return _error_redirect(default_return_to, "state_mismatch")

    return_to = _safe_return_to(state_payload.get("return_to", default_return_to))
    code_verifier = state_payload.get("code_verifier", "")
    expected_state = state_payload.get("state", "")

    # Verify state param matches what we issued.
    received_state = request.query_params.get("state", "")
    if not expected_state or received_state != expected_state:
        log.warning(
            "jmap_callback_state_mismatch",
            expected=expected_state,
            received=received_state,
        )
        return _error_redirect(return_to, "state_mismatch")

    code = request.query_params.get("code", "")
    if not code:
        provider_error = request.query_params.get("error")
        log.warning("jmap_callback_no_code", provider_error=provider_error)
        return _error_redirect(return_to, "code_exchange_failed")

    callback_url = f"{settings.api_base_url.rstrip('/')}/oauth/jmap/callback"

    # --- 2–3. Exchange authorization code for tokens ---
    try:
        metadata = await _get_oidc_metadata()
        token_endpoint = metadata["token_endpoint"]
        async with httpx.AsyncClient(timeout=30.0) as http:
            token_resp = await http.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "client_id": settings.jmap_oauth_client_id,
                    "client_secret": settings.jmap_oauth_client_secret,
                    "redirect_uri": callback_url,
                },
            )
        if token_resp.status_code >= 400:
            log.warning("jmap_callback_token_error", status=token_resp.status_code)
            return _error_redirect(return_to, "code_exchange_failed")

        token_data = token_resp.json()
        access_token: str = token_data.get("access_token", "")
        refresh_token: str | None = token_data.get("refresh_token") or None
        expires_in: int = int(token_data.get("expires_in", 3600))

        if not access_token:
            log.warning("jmap_callback_no_access_token")
            return _error_redirect(return_to, "code_exchange_failed")

    except Exception as exc:
        log.exception("jmap_callback_code_exchange_failed", exc_info=exc)
        return _error_redirect(return_to, "code_exchange_failed")

    # --- 4. Fetch userinfo ---
    try:
        userinfo_url = metadata["userinfo_endpoint"]
        async with httpx.AsyncClient(timeout=30.0) as http:
            userinfo_resp = await http.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if userinfo_resp.status_code >= 400:
            log.warning(
                "jmap_callback_userinfo_error", status=userinfo_resp.status_code
            )
            return _error_redirect(return_to, "code_exchange_failed")

        userinfo = userinfo_resp.json()
        account_email: str = userinfo.get("email", "")
        account_name: str = userinfo.get("name", "") or account_email.split("@")[0]

    except Exception as exc:
        log.exception("jmap_callback_userinfo_failed", exc_info=exc)
        return _error_redirect(return_to, "code_exchange_failed")

    # --- 5. Session probe: verify token is accepted by James ---
    if not settings.jmap_session_url:
        log.warning("jmap_callback_no_session_url")
        return _error_redirect(return_to, "session_probe_failed")

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            session_resp = await http.get(
                settings.jmap_session_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if session_resp.status_code >= 400:
            log.warning(
                "jmap_callback_session_probe_failed", status=session_resp.status_code
            )
            return _error_redirect(return_to, "session_probe_failed")

    except Exception as exc:
        log.exception("jmap_callback_session_probe_exception", exc_info=exc)
        return _error_redirect(return_to, "session_probe_failed")

    # --- 6. Upsert oauth_credential ---
    access_token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    try:
        repository.upsert(
            sentinel_name="mail",
            provider="linagora_lemonldap",
            client_id=settings.jmap_oauth_client_id,
            token_endpoint=token_endpoint,
            session_url=settings.jmap_session_url,
            scope=settings.jmap_oauth_scope,
            refresh_token_enc=encrypt(refresh_token) if refresh_token else None,
            access_token_enc=encrypt(access_token),
            access_token_expires_at=access_token_expires_at,
            account_email=account_email,
            account_name=account_name,
        )
    except Exception as exc:
        log.exception("jmap_callback_upsert_failed", exc_info=exc)
        return _error_redirect(return_to, "code_exchange_failed")

    # --- 7. Delete state cookie + redirect to success ---
    if "?" in return_to:
        success_url = return_to + "&status=connected"
    else:
        success_url = return_to + "?status=connected"

    response = RedirectResponse(url=success_url, status_code=302)
    response.delete_cookie(key=JMAP_STATE_COOKIE, path="/")
    return response


__all__ = ["router"]
