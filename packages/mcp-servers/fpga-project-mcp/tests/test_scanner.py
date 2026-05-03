"""Tier 2 pure-logic unit tests for the scanner.

No LLM calls, no MCP handshake — exercises the Verilog scanner end-to-end
against a minimal fixture project in a scratch directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vibe4fpga_platform import scratch_dir

from fpga_project_mcp.scanner import (
    analyze_naming_conventions,
    build_hierarchy,
    scan,
    search_signal,
)


@pytest.fixture
def tiny_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Spin up a two-module Verilog project for scanner tests.

    Uses :func:`vibe4fpga_platform.scratch_dir` instead of pytest's
    ``tmp_path`` to also smoke-test the platform helper on Windows CI.
    """
    root = scratch_dir("fpga_project_mcp_test_")

    (root / "top.v").write_text(
        """
        module top (
            input  wire clk,
            input  wire rst_n,
            input  wire [7:0] data_in,
            output wire [7:0] data_out
        );
            wire [7:0] stage_q;

            sub_counter u_counter (
                .clk   (clk),
                .rst_n (rst_n),
                .q     (stage_q)
            );

            assign data_out = data_in ^ stage_q;
        endmodule
        """,
        encoding="utf-8",
    )
    (root / "sub_counter.v").write_text(
        """
        module sub_counter (
            input  wire clk,
            input  wire rst_n,
            output reg  [7:0] q
        );
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) q <= 8'b0;
                else        q <= q + 1'b1;
            end
        endmodule
        """,
        encoding="utf-8",
    )
    return root


def test_scan_finds_both_modules(tiny_project: Path) -> None:
    result = scan(str(tiny_project))
    assert "top" in result.modules
    assert "sub_counter" in result.modules
    assert result.modules["top"].instantiates == ["sub_counter"]


def test_hierarchy_picks_top(tiny_project: Path) -> None:
    result = scan(str(tiny_project))
    tree   = build_hierarchy(result, top_module=None)
    assert tree["module"] == "top"
    children = {c["module"] for c in tree.get("children", [])}
    assert "sub_counter" in children


def test_search_signal_hits(tiny_project: Path) -> None:
    result = scan(str(tiny_project))
    hits   = search_signal(result, "rst_n")
    # rst_n appears in both top.v and sub_counter.v.
    files = {Path(h["file"]).name for h in hits}
    assert {"top.v", "sub_counter.v"} <= files


def test_naming_conventions_confidence_filter(tiny_project: Path) -> None:
    result = scan(str(tiny_project))
    conv   = analyze_naming_conventions(result)
    # Implementation detail: only conventions with confidence > 0.70 are returned.
    for key, payload in conv.items():
        assert payload["confidence"] > 0.70, f"{key} leaked below threshold: {payload}"
