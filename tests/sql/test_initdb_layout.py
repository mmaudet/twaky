"""`sql/` is executed verbatim at provisioning time — keep rollbacks out of it.

`docker-compose.yml` mounts `./sql` as `/docker-entrypoint-initdb.d`. On a
fresh volume Postgres runs every `*.sh` directly inside it, alphabetically.
A rollback script left there is therefore part of the install.
"""

from __future__ import annotations

import pathlib

import pytest

SQL_DIR = pathlib.Path(__file__).resolve().parents[2] / "sql"
_ROLLBACK_WORDS = ("downgrade", "rollback", "revert", "drop")


def _initdb_scripts() -> list[pathlib.Path]:
    """Scripts Postgres will execute — top level only, the entrypoint never recurses."""
    return sorted(p for p in SQL_DIR.glob("*.sh") if p.is_file())


def test_no_rollback_script_sits_in_the_initdb_path():
    offenders = [
        p.name
        for p in _initdb_scripts()
        if any(word in p.name.lower() for word in _ROLLBACK_WORDS)
    ]
    assert not offenders, (
        f"{offenders} would run when provisioning a fresh volume. "
        "Rollbacks belong in sql/downgrade/ — see its README."
    )


def test_rollbacks_live_one_level_down():
    """The entrypoint globs `*.sh` and never recurses, so a directory can
    never match. This holds as long as the rollbacks actually live in it."""
    downgrade_dir = SQL_DIR / "downgrade"
    assert downgrade_dir.is_dir()
    assert list(downgrade_dir.glob("*.sh")), (
        "sql/downgrade/ is empty — rollbacks were moved or deleted"
    )


@pytest.mark.parametrize("script", _initdb_scripts(), ids=lambda p: p.name)
def test_initdb_scripts_are_executable(script: pathlib.Path):
    """A non-executable *.sh is silently sourced instead of run by the
    entrypoint, which changes error semantics — keep the bit set."""
    assert script.stat().st_mode & 0o111, f"{script.name} is not executable"
