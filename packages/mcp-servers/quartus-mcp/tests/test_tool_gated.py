"""Tier 3 — happy-path exercise gated on Quartus availability.

Skipped cleanly on every host that does not have ``quartus_sh`` on PATH (or
pointed at via the ``QUARTUS_SH`` env var). When Quartus is present we run
the lightweight ``quartus_environment`` tool end-to-end — it spawns the MCP
over stdio, calls ``quartus_sh --version`` once, and returns the edition.

Keeping it to a single happy-path call matches the plan's Tier 3 guidance:
"Keep it minimal."
"""

from __future__ import annotations

import json

import pytest
import vibe4fpga_platform
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio


_QUARTUS_SH = vibe4fpga_platform.find_tool("quartus_sh", env_var="QUARTUS_SH")


@pytest.mark.skipif(_QUARTUS_SH is None, reason="Quartus absent")
async def test_quartus_environment_reports_edition() -> None:
    """``quartus_environment`` returns an edition label when Quartus is installed.

    Uses the MCP-testkit's ``call_tool`` helper if available; otherwise falls
    back to asserting only that the tool is listed (still verifies gating).
    """
    async with stdio_server_spawn("quartus-mcp") as client:
        names = set(await client.list_tools_names())
        assert "quartus_environment" in names

        call = getattr(client, "call_tool", None)
        if call is None:
            pytest.skip("mcp-testkit exposes no call_tool helper; tool list verified.")

        response = await call("quartus_environment", {})
        payload = _payload(response)
        assert payload.get("available") is True, payload
        assert payload.get("edition") in {"Pro", "Lite", "Standard", "Unknown"}, payload


def _payload(response: object) -> dict:
    """Best-effort extraction of the JSON dict a FastMCP tool returns.

    Works across testkit variants that may surface either the raw dict, a
    ``content`` list with text items, or an mcp.types.CallToolResult.
    """
    if isinstance(response, dict):
        return response
    content = getattr(response, "content", None)
    if content:
        for item in content:
            text = getattr(item, "text", None)
            if text:
                try:
                    return json.loads(text)
                except (TypeError, ValueError):
                    continue
    return {}
