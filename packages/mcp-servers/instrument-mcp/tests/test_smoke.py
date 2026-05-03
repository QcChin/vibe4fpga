"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes the 7 measurement / analysis tools plus the 1 LLM-backed
``analyze_instrument_diff`` skill tool that ``skill.yaml`` promises.
No LLM or VISA calls are made during the test — we only inspect the tool
registry.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_IO_TOOLS = {
    "read_csv_waveform",
    "compute_fft_tool",
    "list_visa_instruments",
    "connect_instrument",
    "capture_live_waveform",
    "align_with_simulation",
    "classify_differences_tool",
}

EXPECTED_SKILL_TOOLS = {
    "analyze_instrument_diff",
}


async def test_handshake() -> None:
    """Initialize completes and the server identifies itself as instrument-mcp."""
    async with stdio_server_spawn("instrument-mcp") as client:
        assert client.server_name == "instrument-mcp"


async def test_list_tools_covers_existing_seven() -> None:
    """All 7 pre-existing waveform I/O + analysis tools are still registered."""
    async with stdio_server_spawn("instrument-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_IO_TOOLS - names
        assert not missing, f"Missing I/O tools: {missing}"


async def test_list_tools_covers_skill_tool() -> None:
    """The new LLM-backed ``analyze_instrument_diff`` tool is registered."""
    async with stdio_server_spawn("instrument-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_SKILL_TOOLS - names
        assert not missing, f"Missing skill tools: {missing}"
