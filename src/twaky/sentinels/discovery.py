"""Auto-discovery of sentinel sub-packages."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twaky.sentinels.base import Sentinel

log = logging.getLogger(__name__)


def discover_sentinels() -> dict[str, type[Sentinel]]:
    """Auto-discovery: sub-packages of twaky.sentinels export SentinelClass.

    Contract:
        src/twaky/sentinels/<name>/sentinel.py must expose SentinelClass,
        a subclass of Sentinel whose classvar .name matches <name>.

    Returns
    -------
    dict[str, type[Sentinel]]
        Mapping of sentinel name to its class. Only includes valid sentinels
        that pass all validation checks.
    """
    # Import the sentinels package to get its __path__
    import twaky.sentinels

    # Import base to avoid circular imports
    from twaky.sentinels.base import Sentinel

    sentinels: dict[str, type[Sentinel]] = {}

    # Walk all submodules in twaky.sentinels
    for modinfo in pkgutil.iter_modules(twaky.sentinels.__path__):
        # Skip non-packages
        if not modinfo.ispkg:
            continue

        # Skip the sources sibling namespace
        if modinfo.name == "sources":
            continue

        pkg_name = modinfo.name
        full_pkg_name = f"twaky.sentinels.{pkg_name}"
        sentinel_module_name = f"{full_pkg_name}.sentinel"

        # Try to import the sentinel module
        try:
            sentinel_module = importlib.import_module(sentinel_module_name)
        except ModuleNotFoundError:
            log.warning(
                f"Package {pkg_name} has no sentinel.py module",
                extra={"pkg": pkg_name},
            )
            continue
        except Exception:  # pylint: disable=broad-except
            log.exception(
                f"Error importing sentinel module for {pkg_name}",
                extra={"pkg": pkg_name},
            )
            continue

        # Check for SentinelClass attribute
        if not hasattr(sentinel_module, "SentinelClass"):
            log.warning(
                f"Package {pkg_name} sentinel.py does not expose SentinelClass",
                extra={"pkg": pkg_name},
            )
            continue

        sentinel_class = sentinel_module.SentinelClass

        # Validate it's a type and a Sentinel subclass
        if not inspect.isclass(sentinel_class):
            log.warning(
                f"SentinelClass in {pkg_name} is not a class",
                extra={"pkg": pkg_name},
            )
            continue

        if not issubclass(sentinel_class, Sentinel):
            log.warning(
                f"SentinelClass in {pkg_name} is not a Sentinel subclass",
                extra={"pkg": pkg_name},
            )
            continue

        # Validate the name matches the package name
        if not hasattr(sentinel_class, "name"):
            log.warning(
                f"SentinelClass in {pkg_name} has no name attribute",
                extra={"pkg": pkg_name},
            )
            continue

        if sentinel_class.name != pkg_name:
            log.warning(
                f"SentinelClass.name ({sentinel_class.name}) does not match "
                f"package name ({pkg_name})",
                extra={"pkg": pkg_name},
            )
            continue

        # All checks passed, add to result
        sentinels[pkg_name] = sentinel_class

    return sentinels


__all__ = ["discover_sentinels"]
