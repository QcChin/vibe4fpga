"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes the 5 deterministic tools this MCP promises. No Vivado / Icarus /
Verilator subprocesses are launched — the handshake only enumerates the
FastMCP tool registry.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_TOOLS = {
    "run_lint",
    "run_synthesis",
    "run_simulation",
    "get_timing_report",
    "classify_errors",
}


async def test_handshake() -> None:
    """Initialize completes and server identifies itself as eda-bridge-mcp."""
    async with stdio_server_spawn("eda-bridge-mcp") as client:
        assert client.server_name == "eda-bridge-mcp"


async def test_list_tools_covers_expected_set() -> None:
    """All 5 subprocess-gateway tools are registered."""
    async with stdio_server_spawn("eda-bridge-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_TOOLS - names
        assert not missing, f"Missing tools: {missing}"
