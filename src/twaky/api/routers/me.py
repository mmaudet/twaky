"""Authenticated user info."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from twaky.api.deps import require_owner
from twaky.config import settings

router = APIRouter(tags=["me"])


@router.get("/me")
def me(email: str = Depends(require_owner)) -> dict[str, str]:
    return {
        "owner_email": email,
        "langfuse_base_url": getattr(settings, "langfuse_host", "") or "",
    }


__all__ = ["router"]
