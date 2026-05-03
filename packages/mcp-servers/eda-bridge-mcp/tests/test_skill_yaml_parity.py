"""Tier 2 — parity between skill.yaml and the live tool list.

eda-bridge-mcp declares ``skills: []`` (no LLM-backed skills), but we still
keep this drift-detection test so that if somebody later adds a skill entry
without wiring the tool, CI catches it. The assertion is a simple
subset check: every ``skills[].tool`` string must appear in the live tool
list served by the running MCP.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio

_SKILL_YAML = Path(__file__).resolve().parents[1] / "skill.yaml"


def _declared_skill_tools() -> set[str]:
    with _SKILL_YAML.open(encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    return {s["tool"] for s in (payload.get("skills") or [])}


async def test_skill_yaml_tools_exist() -> None:
    """Every ``skills[].tool`` in skill.yaml must be exposed by the running server.

    With ``skills: []`` the declared set is empty and the assertion is
    trivially true; the test still spawns the server so adding a skill later
    cannot silently escape drift detection.
    """
    declared = _declared_skill_tools()
    async with stdio_server_spawn("eda-bridge-mcp") as client:
        live = set(await client.list_tools_names())
    missing = declared - live
    assert not missing, (
        f"skill.yaml declares tools that eda-bridge-mcp does not expose: {missing}"
    )
