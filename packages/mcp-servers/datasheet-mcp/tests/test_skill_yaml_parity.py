"""Tier 2 — drift detection between skill.yaml and the live tool list.

datasheet-mcp declares ``skills: []`` because its surface is pure embedding
retrieval, not LLM-chat skills. This test asserts the invariant as a subset
relation: every tool declared in ``skill.yaml`` must be exposed by the
running server (trivially true for an empty declared set, but explicit so
any future skill addition is caught by the same harness that covers the
other MCPs).
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


async def test_skill_yaml_tools_are_subset_of_live_tools() -> None:
    """Every ``skills[].tool`` in skill.yaml must be exposed by the server.

    Empty declared set (datasheet-mcp's current state) satisfies this
    trivially; the test exists so the moment a skill is added the parity
    gate activates.
    """
    declared = _declared_skill_tools()
    async with stdio_server_spawn("datasheet-mcp") as client:
        live = set(await client.list_tools_names())
    missing = declared - live
    assert not missing, (
        f"skill.yaml declares tools that datasheet-mcp does not expose: {missing}"
    )


def test_skill_yaml_declares_no_skills() -> None:
    """Guard the 'no LLM-chat skills' contract.

    datasheet-mcp deliberately does NOT adopt vibe4fpga-llm-client because
    that library only models chat completions, not embeddings. If a skill
    block sneaks in, this test fails and forces a design conversation.
    """
    assert _declared_skill_tools() == set(), (
        "datasheet-mcp is embeddings-only; skill.yaml must keep `skills: []`."
    )
