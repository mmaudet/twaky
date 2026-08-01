"""Heartbeat file bump + probe."""

from __future__ import annotations

import os
import time

from twaky.daemon.heartbeat import bump, is_healthy


def test_bump_creates_file(tmp_path):
    p = tmp_path / "hb"
    bump(str(p))
    assert p.exists()


def test_is_healthy_fresh(tmp_path):
    p = tmp_path / "hb"
    bump(str(p))
    assert is_healthy(str(p), max_age_s=5)


def test_is_healthy_stale(tmp_path):
    p = tmp_path / "hb"
    p.write_bytes(b"")
    old = time.time() - 60
    os.utime(p, (old, old))
    assert not is_healthy(str(p), max_age_s=5)


def test_missing_file_is_unhealthy(tmp_path):
    assert not is_healthy(str(tmp_path / "nope"))
