"""Thread-safe in-process cache for sentinel configuration.

Populated lazily on first read (per-name via ``get()``, or bulk via
``list_enabled()``).  Invalidated by ``config_listener.py`` whenever Postgres
NOTIFYs ``sentinel_changed``.

Design notes
------------
- An ``RLock`` guards ``_by_name`` and ``_enabled_loaded``.
- Load *misses* call the repository **outside** the lock so we don't hold the
  lock over a DB round-trip; the populated entry is then inserted back under
  the lock.
- ``invalidate(name)`` pops the single key **and** resets ``_enabled_loaded``
  because a disabled row's absence changes the enabled set.
- Module singleton ``_registry`` + public accessor ``get_registry()`` — callers
  must use the accessor, not the class directly.
"""

from __future__ import annotations

import threading

from twaky.sentinels import repository
from twaky.sentinels.models import SentinelConfig


class SentinelRegistry:
    """Cache of ``SentinelConfig`` objects keyed by sentinel name."""

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._by_name: dict[str, SentinelConfig] = {}
        self._enabled_loaded: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> SentinelConfig | None:
        """Return the config for *name*, loading from the DB on a cache miss.

        Returns ``None`` if the sentinel does not exist in the DB.
        """
        with self._lock:
            cached = self._by_name.get(name)
            if cached is not None:
                return cached

        # Miss — go to DB outside the lock.
        fetched: SentinelConfig | None = repository.get(name)

        if fetched is None:
            return None

        with self._lock:
            # Another thread may have populated while we were fetching; that's
            # fine — we just overwrite with the same value.
            self._by_name[name] = fetched

        return fetched

    def list_enabled(self) -> list[SentinelConfig]:
        """Return all enabled sentinels, loading from the DB on the first call.

        Subsequent calls return the cached values (filtered by ``enabled``).
        """
        with self._lock:
            if self._enabled_loaded:
                return [v for v in self._by_name.values() if v.enabled]

        # Miss — load from DB outside the lock.
        rows: list[SentinelConfig] = repository.list_enabled()

        with self._lock:
            for row in rows:
                self._by_name[row.name] = row
            self._enabled_loaded = True

        return list(rows)

    def invalidate(self, name: str) -> None:
        """Drop the cache entry for *name* and reset the enabled-set flag.

        The flag reset is necessary because disabling a sentinel changes the
        *set* returned by ``list_enabled()``, not just a single entry.
        """
        with self._lock:
            self._by_name.pop(name, None)
            self._enabled_loaded = False

    def invalidate_all(self) -> None:
        """Clear every cache entry and the enabled-set flag."""
        with self._lock:
            self._by_name.clear()
            self._enabled_loaded = False


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_registry = SentinelRegistry()


def get_registry() -> SentinelRegistry:
    """Return the module-level registry singleton."""
    return _registry


__all__ = ["SentinelRegistry", "get_registry"]
