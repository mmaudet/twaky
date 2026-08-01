"""One-liner wrapper: get_impersonated_token specialised for Plume + JMAP."""

from __future__ import annotations

from twaky.auth import oidc
from twaky.config import settings


def bearer_token_for_owner() -> str:
    """Return a bearer token impersonating the twaky owner for JMAP calls."""
    return oidc.get_impersonated_token(
        settings.twaky_owner_email,
        issuer=settings.plume_oidc_issuer,
        client_id=settings.plume_oidc_client_id,
        client_secret=settings.plume_oidc_client_secret,
    )


__all__ = ["bearer_token_for_owner"]
