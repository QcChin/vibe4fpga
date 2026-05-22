"""Tier 1 smoke tests — stdio handshake + list_tools for the Rust MCP binary.

waveform-mcp-rs absorbed the Python `waveform-mcp` package as of v0.3.0.
The binary now exposes 7 tools: 4 pure-parsing tools, 2 protocol/source
helpers, and 1 LLM-backed skill (`debug_waveform`). The smoke test only
asserts tool registration — the LLM step is exercised by Rust integration
tests gated on ANTHROPIC_* env vars.
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
    "decode_axi",
    "map_signal_to_rtl",
    "debug_waveform",
}


async def test_handshake() -> None:
    """Initialize completes and the Rust server identifies itself."""
    async with stdio_server_spawn("waveform-mcp-rs") as client:
        assert client.server_name == "waveform-mcp-rs"


async def test_list_tools_covers_expected_set() -> None:
    """All 7 tools (4 parsing + 2 helpers + 1 skill) are registered."""
    async with stdio_server_spawn("waveform-mcp-rs") as client:
        names = set(await client.list_tools_names())
        missing = EXPECTED_TOOLS - names
        assert not missing, f"Missing tools: {missing}"
