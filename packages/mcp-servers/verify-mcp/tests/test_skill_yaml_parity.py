"""Tier 2 — drift detection between skill.yaml and the live tool list.

Each skill declared in skill.yaml must be backed by a real MCP tool. This
catches renames or removals that would silently break generated SKILL.md /
command.md / host config files (which are CI-checked separately via
``tools/gen-skills/generate.py --check``).
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
    return {s["tool"] for s in payload.get("skills", [])}


async def test_skill_yaml_tools_exist() -> None:
    """Every `skills[].tool` in skill.yaml must be exposed by the running server."""
    declared = _declared_skill_tools()
    async with stdio_server_spawn("verify-mcp") as client:
        live = set(await client.list_tools_names())
    missing = declared - live
    assert not missing, (
        f"skill.yaml declares tools that verify-mcp does not expose: {missing}"
    )
