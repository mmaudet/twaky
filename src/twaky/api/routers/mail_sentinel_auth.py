"""Mail-sentinel OAuth credential REST endpoints.

Provides 3 endpoints under /mail-sentinel/auth that expose OAuth connection
status, force-refresh, and disconnection to the Twaky owner.
All routes are protected by the ``require_owner`` dependency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import Response

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.oauth import AuthStatus
from twaky.oauth import repository
from twaky.oauth.refresh_manager import RefreshFailed, get_manager

router = APIRouter(prefix="/mail-sentinel/auth", tags=["mail-sentinel-auth"])


# ---------------------------------------------------------------------------
# GET /mail-sentinel/auth
# ---------------------------------------------------------------------------


@router.get("", response_model=AuthStatus)
def get_auth_status(_email: str = Depends(require_owner)) -> AuthStatus:
    """Return the current OAuth connection status for the mail sentinel.

    Returns ``connected=false`` with all nullable fields as ``null`` when no
    credential row exists. Returns ``connected=true`` plus the stored metadata
    fields when a credential row is present (access/refresh tokens are NOT
    included in the response — only metadata).
    """
    cred = repository.get("mail")
    if cred is None:
        return AuthStatus(connected=False)
    return AuthStatus(
        connected=True,
        provider=cred.provider,
        account_email=cred.account_email,
        account_name=cred.account_name,
        session_url=cred.session_url,
        access_token_expires_at=cred.access_token_expires_at,
        last_refresh_at=cred.last_refresh_at,
        last_refresh_error=cred.last_refresh_error,
    )


# ---------------------------------------------------------------------------
# POST /mail-sentinel/auth/refresh
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=AuthStatus)
async def refresh_auth(_email: str = Depends(require_owner)) -> AuthStatus | Response:
    """Force an immediate token refresh and return the updated auth status.

    Returns 409 with code ``oauth_credential_not_found`` if no credential row
    exists. Returns 502 with code ``refresh_failed`` if the token endpoint
    rejects the refresh (e.g. ``invalid_grant``).
    """
    if repository.get("mail") is None:
        return error_response(
            code="oauth_credential_not_found",
            message="no JMAP credential configured",
            status_code=409,
        )
    manager = get_manager("mail")
    try:
        await manager.force_refresh()
    except RefreshFailed as e:
        return error_response(
            code="refresh_failed",
            message=str(e),
            status_code=502,
        )
    # Re-read fresh credential from DB after the refresh
    return get_auth_status(_email=_email)


# ---------------------------------------------------------------------------
# DELETE /mail-sentinel/auth
# ---------------------------------------------------------------------------


@router.delete("", status_code=204)
def delete_auth(_email: str = Depends(require_owner)) -> Response:
    """Delete the OAuth credential for the mail sentinel.

    Idempotent: silently succeeds if no credential row exists.
    The delete trigger fires a NOTIFY on ``oauth_credential_changed`` so the
    in-process sentinel stops polling until the owner reconnects.
    """
    repository.delete("mail")
    return Response(status_code=204)


__all__ = ["router"]
