"""Shared fixtures for agents_config tests."""

from __future__ import annotations

import os
import sys

import pytest


def pytest_sessionstart(session):
    """Called after pytest session starts and collection is done but before test run."""
    # Set localhost for connection (still requires password for remote TCP)
    os.environ["TWAKY_PG_HOST"] = "127.0.0.1"
    # Ensure password is set from env
    if not os.environ.get("TWAKY_PG_PASSWORD"):
        # Fallback to a common test password
        os.environ["TWAKY_PG_PASSWORD"] = "twaky"

    # Reload the settings module so it picks up the new env vars
    if "twaky.config" in sys.modules:
        del sys.modules["twaky.config"]

    # Import settings fresh with new env vars

    # Clear any existing pool so it gets recreated with the new DSN
    import twaky.db

    if twaky.db._pool is not None:
        twaky.db._pool.close()
        twaky.db._pool = None


@pytest.fixture(scope="session", autouse=True)
def cleanup_postgres():
    """Cleanup the pool after tests run."""
    yield
    import twaky.db

    if twaky.db._pool is not None:
        twaky.db._pool.close()
        twaky.db._pool = None
