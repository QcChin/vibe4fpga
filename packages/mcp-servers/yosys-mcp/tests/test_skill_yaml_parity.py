"""Tier 2 — drift detection between skill.yaml and the live tool list.

Every skill declared in ``skill.yaml`` must map to a tool exposed by the
running MCP. ``yosys-mcp`` currently ships zero LLM-backed skills (the tool
surface is pure wrappers around Yosys / nextpnr / Icestorm binaries), so the
expected intersection is empty — the test still guards against a future
skill entry being added without a backing tool implementation.
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


async def test_skill_yaml_tools_exist() -> None:
    """Every `skills[].tool` in skill.yaml must be exposed by the running server."""
    declared = _declared_skill_tools()
    async with stdio_server_spawn("yosys-mcp") as client:
        live = set(await client.list_tools_names())
    missing = declared - live
    assert not missing, (
        f"skill.yaml declares tools that yosys-mcp does not expose: {missing}"
    )
