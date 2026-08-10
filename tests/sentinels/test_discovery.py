"""Tests for sentinel discovery mechanism."""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

from twaky.sentinels.base import Sentinel


def _setup_fake_twaky_structure(tmp_path: Path) -> None:
    """Setup the base twaky/sentinels directory structure with base.py.

    This is called once per test to create the common infrastructure.
    """
    twaky_dir = tmp_path / "twaky"
    twaky_dir.mkdir(exist_ok=True)
    (twaky_dir / "__init__.py").write_text("")

    sentinels_dir = twaky_dir / "sentinels"
    sentinels_dir.mkdir(exist_ok=True)
    (sentinels_dir / "__init__.py").write_text("")

    # Copy base.py to the fake sentinels directory so imports work
    import shutil

    base_src = (
        Path(__file__).parent.parent.parent / "src" / "twaky" / "sentinels" / "base.py"
    )
    base_dst = sentinels_dir / "base.py"
    shutil.copy(base_src, base_dst)


def _install_fake_pkg(
    tmp_path: Path, name: str, class_name_matches: bool = True
) -> None:
    """Install a fake sentinel package in tmp_path for testing.

    Parameters
    ----------
    tmp_path
        The temporary directory where the fake package will be installed.
    name
        The name of the sentinel package (e.g., "fake_a").
    class_name_matches
        If True, SentinelClass.name == name. If False, name is set to
        "wrong_name".
    """
    # Ensure the base structure exists
    _setup_fake_twaky_structure(tmp_path)

    sentinels_dir = tmp_path / "twaky" / "sentinels"
    pkg_dir = sentinels_dir / name
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text("")

    # Determine the class name in the generated module
    class_name = name if class_name_matches else "wrong_name"

    # Create sentinel.py with a synthetic SentinelClass
    sentinel_code = dedent(f'''\
        """Fake sentinel for testing."""
        from twaky.sentinels.base import Outcome, Sentinel

        class SentinelClass(Sentinel):
            """Fake sentinel implementation."""
            name = "{class_name}"
            version = "0.1"
            event_source_kind = "rabbitmq"

            def process(self, event, ctx):
                """Minimal process implementation."""
                return Outcome.PROCESSED
    ''')
    (pkg_dir / "sentinel.py").write_text(sentinel_code)


def test_discovery_finds_matching_sub_packages(tmp_path: Path, monkeypatch) -> None:
    """Discover should find and return valid sentinel packages."""
    # Install two fake sentinel packages
    _install_fake_pkg(tmp_path, "fake_a")
    _install_fake_pkg(tmp_path, "fake_b")

    # Pre-import discovery from the real source before modifying sys.path
    from twaky.sentinels.discovery import discover_sentinels

    # Prepend tmp_path to sys.path (AFTER we've imported discovery)
    monkeypatch.syspath_prepend(str(tmp_path))

    # Evict twaky modules EXCEPT discovery and base, so they re-import from tmp_path
    # but discovery and base stay available from the real source.
    #
    # WHY base is excluded: the fake sentinel.py files in tmp_path import
    # `Sentinel` from `twaky.sentinels.base`.  If we also evict base, it gets
    # re-imported from tmp_path as a *different module object* than the one
    # already held by the test process.  Discovery then sees a `SentinelClass`
    # whose MRO includes the tmp_path copy of `Sentinel`, which is a distinct
    # class from the real `Sentinel` imported at the top of this file — making
    # `issubclass(result["fake_a"], Sentinel)` return False and breaking the
    # assertions below.  Keeping base in sys.modules ensures both the test and
    # the discovered classes share the identical `Sentinel` base class object.
    keys_to_delete = [
        k
        for k in sys.modules
        if k.startswith("twaky")
        and k not in ("twaky.sentinels.discovery", "twaky.sentinels.base")
    ]
    for k in keys_to_delete:
        monkeypatch.delitem(sys.modules, k, raising=False)

    # Run discovery - it will use twaky.sentinels from tmp_path
    result = discover_sentinels()

    # Validate results
    assert "fake_a" in result
    assert "fake_b" in result
    assert len(result) == 2
    assert issubclass(result["fake_a"], Sentinel)
    assert issubclass(result["fake_b"], Sentinel)
    assert result["fake_a"].name == "fake_a"
    assert result["fake_b"].name == "fake_b"


def test_discovery_skips_mismatched_name(tmp_path: Path, monkeypatch, caplog) -> None:
    """Discovery should skip packages where SentinelClass.name doesn't match."""
    # Install a package with mismatched name
    _install_fake_pkg(tmp_path, "bad", class_name_matches=False)

    # Pre-import discovery from the real source before modifying sys.path
    from twaky.sentinels.discovery import discover_sentinels

    # Prepend tmp_path to sys.path (AFTER we've imported discovery)
    monkeypatch.syspath_prepend(str(tmp_path))

    # Evict twaky modules EXCEPT discovery and base, so they re-import from tmp_path
    keys_to_delete = [
        k
        for k in sys.modules
        if k.startswith("twaky")
        and k not in ("twaky.sentinels.discovery", "twaky.sentinels.base")
    ]
    for k in keys_to_delete:
        monkeypatch.delitem(sys.modules, k, raising=False)

    # Run discovery with warning capture
    with caplog.at_level("WARNING"):
        result = discover_sentinels()

    # Validate results
    assert len(result) == 0
    assert any("does not match" in record.message for record in caplog.records)


def test_discovery_skips_package_without_sentinel_py(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """Discovery should skip packages without a sentinel.py module."""
    # Setup the base structure
    _setup_fake_twaky_structure(tmp_path)

    # Create an empty package without sentinel.py
    sentinels_dir = tmp_path / "twaky" / "sentinels"
    empty_dir = sentinels_dir / "empty"
    empty_dir.mkdir(exist_ok=True)
    (empty_dir / "__init__.py").write_text("")

    # Pre-import discovery from the real source before modifying sys.path
    from twaky.sentinels.discovery import discover_sentinels

    # Prepend tmp_path to sys.path (AFTER we've imported discovery)
    monkeypatch.syspath_prepend(str(tmp_path))

    # Evict twaky modules EXCEPT discovery and base, so they re-import from tmp_path
    keys_to_delete = [
        k
        for k in sys.modules
        if k.startswith("twaky")
        and k not in ("twaky.sentinels.discovery", "twaky.sentinels.base")
    ]
    for k in keys_to_delete:
        monkeypatch.delitem(sys.modules, k, raising=False)

    # Run discovery with warning capture
    with caplog.at_level("WARNING"):
        result = discover_sentinels()

    # Validate results
    assert len(result) == 0
    assert any("has no sentinel.py" in record.message for record in caplog.records)
