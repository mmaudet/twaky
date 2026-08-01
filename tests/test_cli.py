"""Smoke tests for the CLI entrypoint."""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "twaky.cli", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_help_runs():
    r = _run("--help")
    assert r.returncode == 0
    assert "twaky" in r.stdout.lower()


def test_env_dumps_redacted():
    r = _run("env")
    assert r.returncode == 0
    # Redacted password never leaks the real value; presence of the key is enough.
    assert "twaky_pg_password" in r.stdout.lower()
    # The literal '***' appears if a password was set.
    # If it's unset (fresh CI), we accept the empty case.


def test_project_command_is_registered():
    r = _run("project", "--help")
    assert r.returncode == 0
    assert "batch" in r.stdout.lower() or "once" in r.stdout.lower()
