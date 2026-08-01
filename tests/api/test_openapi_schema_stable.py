"""CI-side drift detection: docs/api/openapi.yaml matches app.openapi()."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from twaky.api.export_openapi import dump

SCHEMA_PATH = Path(__file__).parent.parent.parent / "docs" / "api" / "openapi.yaml"


def test_committed_schema_matches_app():
    committed = yaml.safe_load(SCHEMA_PATH.read_text())
    live = yaml.safe_load(dump())
    assert committed == live, (
        "docs/api/openapi.yaml is out of sync with the FastAPI app. "
        "Run `make openapi` and commit the result."
    )
