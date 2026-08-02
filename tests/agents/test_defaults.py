"""DEFAULT_PROMPTS module surface tests (pure, no DB)."""

from twaky.agents.defaults import DEFAULT_PROMPTS, DISPLAY_NAMES, ROLES


def test_all_four_agent_ids_present():
    assert set(DEFAULT_PROMPTS.keys()) == {"atlas", "chronos", "plume", "iris"}


def test_no_empty_prompts():
    for agent_id, prompt in DEFAULT_PROMPTS.items():
        assert prompt.strip(), f"{agent_id} has an empty prompt"
        assert len(prompt) <= 8000, f"{agent_id} prompt exceeds 8000 chars"


def test_display_names_map_all_four():
    assert set(DISPLAY_NAMES.keys()) == {"atlas", "chronos", "plume", "iris"}
    assert DISPLAY_NAMES["atlas"] == "Atlas"
    assert DISPLAY_NAMES["chronos"] == "Chronos"
    assert DISPLAY_NAMES["plume"] == "Plume"
    assert DISPLAY_NAMES["iris"] == "Iris"


def test_roles():
    assert ROLES["atlas"] == "orchestrator"
    assert ROLES["chronos"] == "specialist"
    assert ROLES["plume"] == "specialist"
    assert ROLES["iris"] == "specialist"
