"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes every tool declared in ``server.py``. No Quartus invocation occurs —
Quartus itself is absent on the macOS CI leg (excluded by matrix) and may be
absent on Windows CI too.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_TOOLS = {
    # QSF management
    "read_qsf",
    "create_qsf",
    "add_hdl_file",
    "assign_pin",
    # Compilation / JTAG
    "compile_quartus_project",
    "synthesize_quartus",
    "get_timing_report",
    "program_fpga",
    # Environment probe (structured R4 edition detection surface)
    "quartus_environment",
}


async def test_handshake() -> None:
    """Initialize completes and server identifies itself as quartus-mcp."""
    async with stdio_server_spawn("quartus-mcp") as client:
        assert client.server_name == "quartus-mcp"


async def test_list_tools_covers_full_surface() -> None:
    """Every tool declared in server.py is reachable over stdio."""
    async with stdio_server_spawn("quartus-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_TOOLS - names
        assert not missing, f"Missing tools: {missing}"
