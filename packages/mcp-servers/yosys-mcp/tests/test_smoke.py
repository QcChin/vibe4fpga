"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes the 7 Yosys / nextpnr / icepack tools declared by ``server.py``.
No external EDA binaries are invoked.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_TOOLS = {
    "synthesize_ice40",
    "synthesize_ecp5",
    "synthesize_generic",
    "prepare_formal_verification",
    "pnr_ice40",
    "pnr_ecp5",
    "pack_ice40_bitstream",
}


async def test_handshake() -> None:
    """Initialize completes and server identifies itself as yosys-mcp."""
    async with stdio_server_spawn("yosys-mcp") as client:
        assert client.server_name == "yosys-mcp"


async def test_list_tools_covers_expected_set() -> None:
    """All 7 Yosys/nextpnr/icepack tools are registered."""
    async with stdio_server_spawn("yosys-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_TOOLS - names
        assert not missing, f"Missing tools: {missing}"
