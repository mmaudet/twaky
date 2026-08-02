"""Validation layer for skill create/update payloads.

Rules enforced (from spec §6.3):
- name: regex ^[a-z][a-z0-9_]{0,63}$
- description: 1-1000 chars trimmed
- python_source: 1-32000 chars trimmed + ast.parse OK + top-level def run(...)
- bound_agents: subset of {atlas, chronos, plume, iris}
- config_schema: valid JSON Schema Draft 2020-12
- config_values: validates against config_schema
- Empty patch body → ValidationError(field="_body")
"""

from __future__ import annotations

import ast
import re
from typing import Any

import jsonschema
from jsonschema.exceptions import SchemaError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
BOUND_AGENT_IDS = frozenset({"atlas", "chronos", "plume", "iris"})


class ValidationError(Exception):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValidationError("name", "must be a string")
    if not NAME_RE.match(name):
        raise ValidationError(
            "name",
            "must match ^[a-z][a-z0-9_]{0,63}$ (lowercase, digits, underscore; "
            "start with letter; 1-64 chars)",
        )
    return name


def _validate_description(desc: Any) -> str:
    if not isinstance(desc, str):
        raise ValidationError("description", "must be a string")
    trimmed = desc.strip()
    if not (1 <= len(trimmed) <= 1000):
        raise ValidationError("description", "must be 1-1000 characters (trimmed)")
    return trimmed


def _has_top_level_run(tree: ast.Module) -> bool:
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "run"
        ):
            return True
    return False


def _validate_python_source(src: Any) -> str:
    if not isinstance(src, str):
        raise ValidationError("python_source", "must be a string")
    trimmed = src.strip()
    if not (1 <= len(trimmed) <= 32000):
        raise ValidationError("python_source", "must be 1-32000 characters (trimmed)")
    try:
        tree = ast.parse(trimmed, mode="exec")
    except SyntaxError as exc:
        raise ValidationError(
            "python_source",
            f"SyntaxError at line {exc.lineno}, col {exc.offset}: {exc.msg}",
        ) from exc
    if not _has_top_level_run(tree):
        raise ValidationError(
            "python_source",
            "module must define a top-level 'def run(...)' function",
        )
    return trimmed


def _validate_bound_agents(agents: Any) -> list[str]:
    if not isinstance(agents, list):
        raise ValidationError("bound_agents", "must be an array")
    bad = [a for a in agents if a not in BOUND_AGENT_IDS]
    if bad:
        raise ValidationError(
            "bound_agents",
            f"unknown agent ids: {bad}. Allowed: {sorted(BOUND_AGENT_IDS)}",
        )
    return list(agents)


def _validate_json_schema(schema: Any) -> dict:
    if not isinstance(schema, dict):
        raise ValidationError("config_schema", "must be an object")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValidationError(
            "config_schema", f"invalid JSON Schema: {exc.message}"
        ) from exc
    return schema


def _validate_config_values(schema: dict, values: Any) -> dict:
    if not isinstance(values, dict):
        raise ValidationError("config_values", "must be an object")
    try:
        jsonschema.validate(values, schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(
            "config_values",
            f"does not match config_schema: {exc.message}",
        ) from exc
    return values


def validate_create(body: dict) -> dict:
    """Full validation for POST /skills. Returns normalized dict."""
    if not isinstance(body, dict):
        raise ValidationError("_body", "must be an object")
    normalized = {
        "name": _validate_name(body.get("name")),
        "description": _validate_description(body.get("description")),
        "python_source": _validate_python_source(body.get("python_source")),
        "bound_agents": _validate_bound_agents(body.get("bound_agents", [])),
        "enabled": bool(body.get("enabled", True)),
    }
    schema = _validate_json_schema(body.get("config_schema", {}))
    values = _validate_config_values(schema, body.get("config_values", {}))
    normalized["config_schema"] = schema
    normalized["config_values"] = values
    return normalized


def validate_patch(body: dict) -> dict:
    """Partial validation for PATCH /skills/{id}. Empty body → ValidationError."""
    if not isinstance(body, dict):
        raise ValidationError("_body", "must be an object")
    if not body:
        raise ValidationError("_body", "at least one field required")

    patch: dict[str, Any] = {}
    if "name" in body:
        patch["name"] = _validate_name(body["name"])
    if "description" in body:
        patch["description"] = _validate_description(body["description"])
    if "python_source" in body:
        patch["python_source"] = _validate_python_source(body["python_source"])
    if "bound_agents" in body:
        patch["bound_agents"] = _validate_bound_agents(body["bound_agents"])
    if "enabled" in body:
        patch["enabled"] = bool(body["enabled"])
    if "config_schema" in body or "config_values" in body:
        # This service is stateless: it never fetches the persisted schema from storage.
        # When the PATCH includes config_values without config_schema, validation runs
        # against {} (empty schema, accepts anything). If the caller wants values
        # validated against the current persisted schema, it must include config_schema
        # in the same PATCH body.
        schema = _validate_json_schema(
            body.get("config_schema", {}) if "config_schema" in body else {}
        )
        if "config_schema" in body:
            patch["config_schema"] = schema
        if "config_values" in body:
            # Validate values against the incoming schema if present, else empty
            # schema (which accepts anything).
            patch["config_values"] = _validate_config_values(
                schema, body["config_values"]
            )
    return patch


__all__ = [
    "BOUND_AGENT_IDS",
    "NAME_RE",
    "ValidationError",
    "validate_create",
    "validate_patch",
]
