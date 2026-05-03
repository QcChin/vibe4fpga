"""Tier 3 — end-to-end smoke on a tiny counter module.

Skipped if ``yosys`` is not on PATH / ``YOSYS_PATH``. On machines with Yosys
installed (``brew install yosys`` / oss-cad-suite) this verifies that the
platform subprocess wrapper + ice40 synth script template produce a JSON
netlist without errors.
"""

from __future__ import annotations

import pytest
from vibe4fpga_platform import find_tool, scratch_dir

from yosys_mcp.synth import synthesize

pytestmark = pytest.mark.asyncio


_COUNTER_SV = """\
module counter (
    input  wire       clk,
    input  wire       rst_n,
    output reg  [3:0] cnt
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cnt <= 4'd0;
        else
            cnt <= cnt + 4'd1;
    end
endmodule
"""


@pytest.mark.skipif(find_tool("yosys") is None, reason="yosys absent")
async def test_ice40_synthesis_of_counter() -> None:
    wd = scratch_dir("yosys_test_counter_")
    src = wd / "counter.v"
    src.write_text(_COUNTER_SV, encoding="utf-8")

    result = await synthesize(
        source_files=[str(src)],
        top_module="counter",
        target="ice40",
        work_dir=str(wd),
        sv_mode=False,
        timeout=60,
    )

    assert result["success"], f"Yosys errors: {result['errors']}"
    assert result["output_json"] is not None
    assert result["returncode"] == 0
