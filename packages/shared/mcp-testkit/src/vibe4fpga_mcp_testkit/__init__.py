"""vibe4fpga-mcp-testkit — shared smoke-test harness for MCP servers.

Intended solely for the ``tests/`` directory of each MCP server in the
vibe4fpga monorepo. Not published to PyPI.

Typical usage::

    # packages/mcp-servers/fpga-project-mcp/tests/test_smoke.py
    import pytest
    from vibe4fpga_mcp_testkit import stdio_server_spawn

    pytestmark = pytest.mark.asyncio

    async def test_tool_list():
        async with stdio_server_spawn("fpga-project-mcp") as client:
            tools = await client.list_tools_names()
            assert "scan_project" in tools

    async def test_handshake():
        async with stdio_server_spawn("fpga-project-mcp") as client:
            # initialize + initialized are performed by the harness; a
            # successful context-manager enter means the handshake succeeded.
            assert client.server_name == "fpga-project-mcp"
"""

from __future__ import annotations

from .harness import MCPSmokeClient, stdio_server_spawn

__all__ = ["stdio_server_spawn", "MCPSmokeClient"]
__version__ = "0.1.0"
