"""Validation matrix for skills_config.service."""

from __future__ import annotations

import pytest

from twaky.skills_config.service import (
    BOUND_AGENT_IDS,
    ValidationError,
    validate_create,
    validate_patch,
)

VALID_BODY = {
    "name": "echo",
    "description": "Echo tool",
    "python_source": "def run(**kwargs):\n    return str(kwargs)",
    "bound_agents": ["atlas"],
}


# ---- name ----


@pytest.mark.parametrize("name", ["echo", "search_wikipedia", "a", "a1", "z_9_"])
def test_valid_names(name):
    body = {**VALID_BODY, "name": name}
    assert validate_create(body)["name"] == name


@pytest.mark.parametrize(
    "name",
    ["", "Echo", "1abc", "with-hyphen", "with space", "a" * 65, "sendEmail"],
)
def test_invalid_names_rejected(name):
    body = {**VALID_BODY, "name": name}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "name"


# ---- description ----


def test_description_trimmed():
    body = {**VALID_BODY, "description": "   hello   "}
    assert validate_create(body)["description"] == "hello"


def test_description_empty_after_trim_rejected():
    body = {**VALID_BODY, "description": "     "}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "description"


def test_description_too_long():
    body = {**VALID_BODY, "description": "x" * 1001}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "description"


# ---- python_source ----


def test_python_syntax_error_rejected():
    body = {**VALID_BODY, "python_source": "def run("}  # unterminated
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"
    assert "SyntaxError" in exc.value.message


def test_missing_run_function_rejected():
    body = {**VALID_BODY, "python_source": "def other():\n    return 1"}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"
    assert "def run" in exc.value.message


def test_run_as_lambda_rejected():
    body = {**VALID_BODY, "python_source": "run = lambda **kw: 1"}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"


def test_run_as_async_function_accepted():
    body = {**VALID_BODY, "python_source": "async def run(**kw):\n    return 1"}
    assert validate_create(body)["python_source"].startswith("async def run")


def test_python_source_too_long():
    body = {**VALID_BODY, "python_source": "def run():\n    " + ("x = 1\n    " * 4000)}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"


# ---- bound_agents ----


def test_bound_agents_subset_ok():
    body = {**VALID_BODY, "bound_agents": ["atlas", "plume"]}
    assert validate_create(body)["bound_agents"] == ["atlas", "plume"]


def test_bound_agents_empty_list_ok():
    body = {**VALID_BODY, "bound_agents": []}
    assert validate_create(body)["bound_agents"] == []


def test_bound_agents_unknown_id_rejected():
    body = {**VALID_BODY, "bound_agents": ["atlas", "zeus"]}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "bound_agents"


def test_bound_agents_wrong_type_rejected():
    body = {**VALID_BODY, "bound_agents": "atlas"}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "bound_agents"


def test_bound_agent_ids_constant_matches_spec():
    assert BOUND_AGENT_IDS == frozenset({"atlas", "chronos", "plume", "iris"})


# ---- config_schema + config_values ----


def test_valid_json_schema_and_matching_values():
    body = {
        **VALID_BODY,
        "config_schema": {
            "type": "object",
            "properties": {"endpoint": {"type": "string"}},
            "required": ["endpoint"],
        },
        "config_values": {"endpoint": "https://x"},
    }
    result = validate_create(body)
    assert result["config_values"] == {"endpoint": "https://x"}


def test_invalid_json_schema_rejected():
    body = {**VALID_BODY, "config_schema": {"type": "not-a-real-type"}}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "config_schema"


def test_config_values_not_matching_schema_rejected():
    body = {
        **VALID_BODY,
        "config_schema": {
            "type": "object",
            "properties": {"endpoint": {"type": "string"}},
            "required": ["endpoint"],
        },
        "config_values": {},  # missing "endpoint"
    }
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "config_values"


# ---- patch ----


def test_patch_empty_body_rejected():
    with pytest.raises(ValidationError) as exc:
        validate_patch({})
    assert exc.value.field == "_body"


def test_patch_single_field_ok():
    assert validate_patch({"description": "new"}) == {"description": "new"}


def test_patch_unknown_field_ignored_or_kept_pure():
    # Service layer accepts unknown top-level keys silently — repository
    # layer is the one that rejects unknown columns (see T2 test).
    # This isolates responsibilities: service = shape/rules, repo = column names.
    result = validate_patch({"description": "x", "extraneous": 1})
    assert result == {"description": "x"}


def test_patch_config_values_validated_against_new_schema():
    result = validate_patch(
        {
            "config_schema": {
                "type": "object",
                "required": ["k"],
                "properties": {"k": {"type": "string"}},
            },
            "config_values": {"k": "v"},
        }
    )
    assert result["config_values"] == {"k": "v"}
