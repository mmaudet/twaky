"""Dump the current OpenAPI schema as YAML."""

from __future__ import annotations

import sys

import yaml  # type: ignore[import-untyped]

from twaky.api.main import app


def dump() -> str:
    return yaml.safe_dump(
        app.openapi(),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


if __name__ == "__main__":
    sys.stdout.write(dump())
