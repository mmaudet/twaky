"""Shared fixtures for API tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
