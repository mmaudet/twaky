"""The seed script INSERTed prompts must carry exactly the same prompt text as DEFAULT_PROMPTS.

Catches the drift bug: someone edits _SYSTEM (which no longer exists —
the module was refactored in T7), forgets to touch defaults.py + the SQL
seed. If those two ever diverge, the reset button lies about defaults.
"""

import re
from pathlib import Path

from twaky.agents.defaults import DEFAULT_PROMPTS

SEED_FILE = Path(__file__).parents[2] / "sql" / "006_init_agents.sh"


def _extract_prompts_from_seed() -> dict[str, str]:
    """Parse INSERTed prompts by locating dollar-quote markers per agent."""
    content = SEED_FILE.read_text()
    prompts = {}
    for agent_id in ("atlas", "chronos", "plume", "iris"):
        # The heredoc pattern places the prompt between the two ATLAS_EOF (etc.) markers.
        marker = f"{agent_id.upper()}_EOF"
        pattern = rf"<<'{marker}'\n(.*?)\n{marker}"
        match = re.search(pattern, content, re.DOTALL)
        assert match is not None, f"no heredoc block for {agent_id} in {SEED_FILE}"
        prompts[agent_id] = match.group(1)
    return prompts


def test_seed_prompts_match_defaults():
    seed = _extract_prompts_from_seed()
    for agent_id, expected in DEFAULT_PROMPTS.items():
        assert seed[agent_id] == expected, (
            f"{agent_id}: seed script prompt diverges from defaults.py.\n"
            f"  seed: {seed[agent_id][:80]!r}\n"
            f"  defaults: {expected[:80]!r}"
        )


def test_seed_script_is_executable():
    assert SEED_FILE.stat().st_mode & 0o111, f"{SEED_FILE} is not executable"
