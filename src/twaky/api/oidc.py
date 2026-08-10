"""authlib OAuth client factory for LemonLDAP-NG."""

from __future__ import annotations

from functools import lru_cache

from authlib.integrations.starlette_client import OAuth  # type: ignore[import-untyped]

from twaky.config import settings


@lru_cache(maxsize=1)
def oauth_client() -> OAuth:
    """Return a lazily-constructed OAuth client registered with the twaky-api provider."""
    oauth = OAuth()
    oauth.register(
        name="twaky_api",
        client_id=settings.api_oidc_client_id,
        client_secret=settings.api_oidc_client_secret,
        server_metadata_url=(
            settings.api_oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


@lru_cache(maxsize=1)
def jmap_oauth_client() -> OAuth:
    """Return a lazily-constructed OAuth client registered with the JMAP mail sentinel provider."""
    oauth = OAuth()
    oauth.register(
        name="jmap_sentinel",
        client_id=settings.jmap_oauth_client_id,
        client_secret=settings.jmap_oauth_client_secret,
        server_metadata_url=(
            settings.jmap_oauth_issuer.rstrip("/") + "/.well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": settings.jmap_oauth_scope,
            "code_challenge_method": "S256",  # PKCE mandatory
        },
    )
    return oauth


__all__ = ["jmap_oauth_client", "oauth_client"]
