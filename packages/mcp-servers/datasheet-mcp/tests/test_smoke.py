"""Tier 1 smoke tests — stdio handshake + list_tools.

Verifies the server starts, completes the MCP initialize handshake, and
exposes the 6 static retrieval tools. No OpenAI or Qdrant calls are made;
tool registration happens before any embedding-backend is instantiated.
"""

from __future__ import annotations

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


EXPECTED_TOOLS = {
    "search_datasheet",
    "search_protocol",
    "search_design_pattern",
    "get_ip_interface",
    "index_document_tool",
    "search_all",
}


async def test_handshake() -> None:
    """Initialize completes and server identifies itself as datasheet-mcp."""
    async with stdio_server_spawn("datasheet-mcp") as client:
        assert client.server_name == "datasheet-mcp"


async def test_list_tools_covers_expected_set() -> None:
    """All 6 retrieval tools are registered."""
    async with stdio_server_spawn("datasheet-mcp") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_TOOLS - names
        assert not missing, f"Missing retrieval tools: {missing}"
