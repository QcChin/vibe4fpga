"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes the 5 parsing/compression tools + 1 LLM-backed skill tool that
skill.yaml promises. No LLM calls are made.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_PARSING_TOOLS = {
    "parse_waveform_tool",
    "extract_signal_events",
    "decode_axi_tool",
    "summarize_for_llm",
    "map_signal_to_rtl",
}

EXPECTED_SKILL_TOOLS = {
    "debug_waveform",
}


async def test_handshake() -> None:
    """Initialize completes and server identifies itself as waveform-mcp."""
    async with stdio_server_spawn("waveform-mcp") as client:
        assert client.server_name == "waveform-mcp"


async def test_list_tools_covers_parsing_set() -> None:
    """All 5 parsing / compression tools are registered."""
    async with stdio_server_spawn("waveform-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_PARSING_TOOLS - names
        assert not missing, f"Missing parsing tools: {missing}"


async def test_list_tools_covers_skill_set() -> None:
    """The waveform_debug skill tool is registered."""
    async with stdio_server_spawn("waveform-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_SKILL_TOOLS - names
        assert not missing, f"Missing skill tools: {missing}"
