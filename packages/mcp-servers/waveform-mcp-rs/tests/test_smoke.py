"""Tier 1 smoke tests — stdio handshake + list_tools for the Rust MCP binary.

Unlike the Python siblings, waveform-mcp-rs is a Rust binary. The
``vibe4fpga_mcp_testkit`` harness spawns any stdio MCP the same way
(``command = <name>`` on PATH), so the test shape is identical — we just
skip when ``cargo build --release`` has not produced the binary.

The four tools asserted here mirror src/main.rs `list_tools`; no LLM calls
are made, so there is no skill-tool counterpart.
"""

from __future__ import annotations

import shutil

import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        shutil.which("waveform-mcp-rs") is None,
        reason="Rust binary not built — run `cargo build --release` first, "
        "then add target/release to PATH (or `cargo install --path .`).",
    ),
]


EXPECTED_TOOLS = {
    "parse_waveform",
    "extract_signal_events",
    "get_signal_stats",
    "summarize_waveform",
}


async def test_handshake() -> None:
    """Initialize completes and the Rust server identifies itself."""
    async with stdio_server_spawn("waveform-mcp-rs") as client:
        assert client.server_name == "waveform-mcp-rs"


async def test_list_tools_covers_expected_set() -> None:
    """All 4 pure VCD-parsing tools are registered."""
    async with stdio_server_spawn("waveform-mcp-rs") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_TOOLS - names
        assert not missing, f"Missing tools: {missing}"
