"""Smoke tests: skills config_listener and registry are wired into atlas_daemon."""

from __future__ import annotations

from pathlib import Path


def test_skills_config_listener_is_spawned_in_main_loop():
    src = Path("src/twaky/daemon/atlas_daemon.py").read_text()
    # SP4 agents listener should still be present.
    assert "agents_config_listener.run(stop)" in src, "SP4 agents listener missing"
    # SP5 skills listener must be present.
    assert "skills_config_listener.run(stop)" in src, "SP5 skills listener missing"


def test_skills_registry_invalidate_at_boot():
    src = Path("src/twaky/daemon/atlas_daemon.py").read_text()
    assert "skills_registry.invalidate_all()" in src
