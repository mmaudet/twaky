"""twaky mission CLI subcommands."""

from __future__ import annotations

import subprocess
import sys


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "twaky.cli", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_mission_help_lists_subcommands():
    r = _run("mission", "--help")
    assert r.returncode == 0
    for sub in ["declare", "list", "show", "resume", "cancel"]:
        assert sub in r.stdout


def test_atlas_help():
    r = _run("atlas", "--help")
    assert r.returncode == 0
    assert "run" in r.stdout
    assert "health" in r.stdout
