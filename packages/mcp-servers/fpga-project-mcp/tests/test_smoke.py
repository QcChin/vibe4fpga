"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes the 6 static tools + 3 LLM-backed skill tools the skill.yaml manifest
promises. No LLM calls are made.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_STATIC_TOOLS = {
    "scan_project",
    "get_hierarchy",
    "search_signal_tool",
    "analyze_naming_conventions_tool",
    "get_interface_neighbors",
    "find_similar_modules",
}

EXPECTED_SKILL_TOOLS = {
    "spec_to_rtl",
    "review_rtl",
    "suggest_timing_fix",
}


async def test_handshake() -> None:
    """Initialize completes and server identifies itself as fpga-project-mcp."""
    async with stdio_server_spawn("fpga-project-mcp") as client:
        assert client.server_name == "fpga-project-mcp"


async def test_list_tools_covers_static_set() -> None:
    """All 6 static (scanner-only) tools are registered."""
    async with stdio_server_spawn("fpga-project-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_STATIC_TOOLS - names
        assert not missing, f"Missing static tools: {missing}"


async def test_list_tools_covers_skill_set() -> None:
    """All 3 LLM-backed skill tools are registered."""
    async with stdio_server_spawn("fpga-project-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_SKILL_TOOLS - names
        assert not missing, f"Missing skill tools: {missing}"
