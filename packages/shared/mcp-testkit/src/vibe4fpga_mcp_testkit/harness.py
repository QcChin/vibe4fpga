"""Async context manager that spawns an MCP server over stdio and yields a
minimal client for Tier 1 smoke tests.

Wraps the official ``mcp`` SDK (``mcp.client.stdio.stdio_client`` +
``mcp.ClientSession``) so MCP servers can be smoke-tested in four lines of
pytest without each package re-implementing the boilerplate.

Only concerned with the bare MCP handshake + ``list_tools`` / ``call_tool``
shape. Anything richer (fixtures, recorded LLM fixtures, VCD data) lives in
the individual MCP's ``tests/`` directory.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass
class MCPSmokeClient:
    """Thin convenience wrapper over :class:`mcp.ClientSession`."""

    session:     ClientSession
    server_name: str

    async def list_tools_names(self) -> list[str]:
        """Return just the tool ``name`` strings (Tier 1 assertion target)."""
        result = await self.session.list_tools()
        return [t.name for t in result.tools]

    async def list_tools(self) -> list[Any]:
        """Return the full tool descriptors."""
        result = await self.session.list_tools()
        return list(result.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke one tool by name; returns the raw :class:`CallToolResult`."""
        return await self.session.call_tool(name, arguments or {})


@asynccontextmanager
async def stdio_server_spawn(
    command: str | list[str],
    *,
    args: list[str] | None = None,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> AsyncIterator[MCPSmokeClient]:
    """Spawn an MCP server over stdio and yield a connected smoke client.

    Args:
        command: Either the raw binary name (``"fpga-project-mcp"``) or a
                 full argv list. Strings are split on whitespace.
        args:    Extra argv appended to ``command`` (no shell interpolation).
        cwd:     Optional working directory for the child process.
        env:     Optional extra env vars merged into the inherited environment.

    Yields:
        :class:`MCPSmokeClient` after a successful ``initialize`` handshake.
        The child process is torn down on context-manager exit.

    Raises:
        Whatever the MCP SDK raises on handshake failure — tests should let
        it propagate so pytest reports the real cause.
    """
    if isinstance(command, str):
        argv = command.split()
    else:
        argv = list(command)
    if args:
        argv.extend(args)

    params = StdioServerParameters(
        command=argv[0],
        args=argv[1:] if len(argv) > 1 else [],
        cwd=str(cwd) if cwd else None,
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            server_name = _extract_server_name(init_result) or argv[0]
            yield MCPSmokeClient(session=session, server_name=server_name)


def _extract_server_name(init_result: Any) -> str | None:
    """Pull ``serverInfo.name`` off the initialize result across SDK versions."""
    # Newer SDK shape.
    info = getattr(init_result, "server_info", None) or getattr(init_result, "serverInfo", None)
    if info is not None:
        name = getattr(info, "name", None)
        if isinstance(name, str):
            return name
    # Dict-shape fallback.
    if isinstance(init_result, dict):
        server_info = init_result.get("serverInfo") or init_result.get("server_info") or {}
        name = server_info.get("name")
        if isinstance(name, str):
            return name
    return None


__all__ = ["MCPSmokeClient", "stdio_server_spawn"]


# Guard against accidental use on ancient Python that lacks asyncio.TaskGroup
# (the MCP SDK relies on it).
if sys.version_info < (3, 11):  # pragma: no cover
    raise RuntimeError("vibe4fpga-mcp-testkit requires Python >= 3.11")
