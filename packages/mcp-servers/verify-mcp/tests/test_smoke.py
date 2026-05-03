"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes the two LLM-backed skill tools that ``skill.yaml`` promises. No LLM
calls are made — only the tool schema is introspected.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_SKILL_TOOLS = {
    "generate_testbench",
    "score_verification",
}


async def test_handshake() -> None:
    """Initialize completes and server identifies itself as verify-mcp."""
    async with stdio_server_spawn("verify-mcp") as client:
        assert client.server_name == "verify-mcp"


async def test_list_tools_covers_skill_set() -> None:
    """Both LLM-backed skill tools are registered."""
    async with stdio_server_spawn("verify-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_SKILL_TOOLS - names
        assert not missing, f"Missing skill tools: {missing}"
