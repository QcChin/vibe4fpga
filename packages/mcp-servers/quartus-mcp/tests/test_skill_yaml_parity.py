"""Tier 2 — drift detection between skill.yaml and the live tool list.

``quartus-mcp`` declares zero skills (no LLM-backed tools), so this test
simply pins that invariant: if someone adds an entry to ``skill.yaml`` they
must also expose a matching MCP tool, or vice versa.
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
    return {s["tool"] for s in payload.get("skills", []) or []}


def test_skill_yaml_has_no_skills() -> None:
    """quartus-mcp is a pure tool-bridge MCP — no LLM skills declared."""
    assert _declared_skill_tools() == set(), (
        "quartus-mcp is not expected to declare any skills. If you added "
        "one, update this test and provide a corresponding MCP tool."
    )


async def test_skill_yaml_tools_exist() -> None:
    """Each declared skill (currently: none) must map to a live tool."""
    declared = _declared_skill_tools()
    async with stdio_server_spawn("quartus-mcp") as client:
        live = set(await client.list_tools_names())
    missing = declared - live
    assert not missing, (
        f"skill.yaml declares tools that quartus-mcp does not expose: {missing}"
    )
