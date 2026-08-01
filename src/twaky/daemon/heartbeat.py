"""File-based heartbeat used by the Docker healthcheck."""

from __future__ import annotations

import os
import time
from pathlib import Path

_DEFAULT = "/tmp/atlas.heartbeat"


def bump(path: str = _DEFAULT) -> None:
    p = Path(path)
    p.touch(exist_ok=True)
    os.utime(p, None)


def is_healthy(path: str = _DEFAULT, max_age_s: int = 30) -> bool:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    return (time.time() - mtime) <= max_age_s


__all__ = ["bump", "is_healthy"]
