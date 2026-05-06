"""Demo driver — directly calls the RTL generator + review_rtl for the
UART RX demo. Skips the full spec2rtl 5-stage pipeline (which expects
structured JSON intermediates that are brittle for NL specs with FSMs).

Delete after use — this is a demo artifact, not production plumbing."""

from __future__ import annotations

import asyncio
import json
import pathlib
import re

from fpga_project_mcp.skills.code_review.checker import review_code
from fpga_project_mcp.skills._llm import call_llm
from fpga_project_mcp.skills.spec2rtl.prompts import RTL_GENERATOR_SYSTEM

ROOT = pathlib.Path(r"E:\projects\vibe4fpga\examples\demo_uart_rx")
RTL  = ROOT / "rtl"
REV  = ROOT / "docs" / "fpga" / "04_rtl_review"

# Project naming convention (from prior `analyze_naming_conventions_tool` run)
PROJECT_CONTEXT = """\
Project naming convention detected:
- Clock signal:  `clk` (confidence 1.0)
- Reset signal:  `rst_n` (active-low) (confidence 0.75)
All generated modules must use these names for top-level clk/rst ports.
"""


SPECS = {
    "uart_baud_gen": """\
Module name: uart_baud_gen

Purpose: Produce a 1-cycle-wide tick pulse every N sys_clks where N is a
parameter. Used by a UART receiver for 16x oversampling; with defaults
tick fires at 115200 * 16 = 1.8432 MHz from a 50 MHz system clock.

Parameters:
- CLK_HZ     (default 50_000_000): system clock frequency
- BAUD_HZ    (default 115200):    target UART baud rate
- OVERSAMPLE (default 16):         oversampling factor

Internal: localparam int DIV = CLK_HZ / (BAUD_HZ * OVERSAMPLE); DIV must be >= 1.
Internal: localparam int CNT_W = (DIV > 1) ? $clog2(DIV) : 1;

Ports:
- input  wire clk
- input  wire rst_n   (active-low SYNCHRONOUS reset)
- output reg  tick    (1-cycle pulse)

Behavior:
- On reset (rst_n=0): counter = 0, tick = 0.
- On each posedge clk with rst_n=1: if counter == DIV-1, wrap counter to 0
  and assert tick for that one cycle; otherwise increment counter and keep
  tick = 0. tick is REGISTERED.
""",

    "uart_rx": """\
Module name: uart_rx

Purpose: Receive standard UART 8-N-1 frames and deliver bytes with a
one-cycle rx_valid pulse. Assumes an external uart_baud_gen producing a
tick pulse at OVERSAMPLE x the baud rate.

Parameters:
- OVERSAMPLE (default 16): must be a power-of-two >= 4
- Internal: localparam int CNT_W = $clog2(OVERSAMPLE + 1);

Ports:
- input  wire        clk
- input  wire        rst_n           (active-low SYNCHRONOUS reset)
- input  wire        tick            (1-cycle pulse from uart_baud_gen)
- input  wire        rx_in           (serial pin, async)
- output reg  [7:0]  rx_data
- output reg         rx_valid        (1-cycle pulse on good byte)
- input  wire        rx_ready        (NO EFFECT on FSM — see note)
- output reg         rx_frame_error  (1-cycle pulse on bad stop bit)

rx_ready semantics: demo simplification. rx_valid is ALWAYS asserted for
exactly one cycle regardless of rx_ready. Consumer must read rx_data on
that cycle or the byte is dropped. Wire rx_ready in but do NOT use it
internally.

Synchronization: rx_in is asynchronous. Use a 2-FF synchronizer:
rx_in_meta (1st flop) and rx_in_sync (2nd flop). All downstream FSM logic
uses rx_in_sync.

Start detection: level-detect in IDLE. If rx_in_sync == 0 on a posedge
clk while state == S_IDLE, transition to S_START and set tick_cnt = 0.

FSM states (binary-encoded 4 bits):
  localparam [3:0] S_IDLE  = 4'd0,
                   S_START = 4'd1,
                   S_D0    = 4'd2, S_D1 = 4'd3, S_D2 = 4'd4, S_D3 = 4'd5,
                   S_D4    = 4'd6, S_D5 = 4'd7, S_D6 = 4'd8, S_D7 = 4'd9,
                   S_STOP  = 4'd10;

Counter: reg [CNT_W-1:0] tick_cnt.
Shift register: reg [7:0] shift_reg.

State-by-state behavior:
- S_IDLE: outputs deasserted. If rx_in_sync == 0, state <= S_START,
  tick_cnt <= 0.
- S_START: if tick == 1 then tick_cnt <= tick_cnt + 1. When tick_cnt
  reaches (OVERSAMPLE/2 - 1) AND tick == 1 (this is the mid-start-bit
  sample), check rx_in_sync: if 0 confirm start → state <= S_D0,
  tick_cnt <= 0; if 1 it was a glitch → state <= S_IDLE.
- S_D0..S_D7: if tick == 1 then tick_cnt <= tick_cnt + 1. When tick_cnt
  reaches (OVERSAMPLE - 1) AND tick == 1 (mid of next bit), shift
  rx_in_sync into the LSB of the shift register shifting RIGHT:
  shift_reg <= {rx_in_sync, shift_reg[7:1]}. Then tick_cnt <= 0 and
  state advances to the next data state (S_D7 → S_STOP).
- S_STOP: if tick == 1 then tick_cnt <= tick_cnt + 1. When tick_cnt
  reaches (OVERSAMPLE - 1) AND tick == 1, sample rx_in_sync:
    - if 1 (stop bit ok): rx_data <= shift_reg; rx_valid <= 1 for one
      cycle; state <= S_IDLE.
    - if 0 (framing error): rx_frame_error <= 1 for one cycle; rx_data
      unchanged; state <= S_IDLE.
- Both rx_valid and rx_frame_error are pulse-1 outputs: they must be
  deasserted on the cycle AFTER they fire. Easiest pattern: drive them
  to 0 every cycle by default, only set them to 1 in the cycle the stop
  sample fires.

On reset (rst_n=0 sampled synchronously):
- state = S_IDLE
- rx_data = 0, rx_valid = 0, rx_frame_error = 0
- tick_cnt = 0, shift_reg = 0
- rx_in_meta = 1, rx_in_sync = 1 (line idle state)
""",
}


async def generate_rtl(spec: str, module_name: str) -> str:
    """Direct call to RTL_GENERATOR_SYSTEM. Returns Verilog source, stripped
    of any accidental markdown fences."""
    system = RTL_GENERATOR_SYSTEM.format(context=PROJECT_CONTEXT)
    user   = (
        f"Generate synthesizable RTL for this module. Follow every detail "
        f"of the spec below.\n\nMODULE SPEC:\n{spec}\n\n"
        f"Output ONLY the Verilog module source. No explanation, no markdown."
    )
    raw = await call_llm(
        messages=[{"role": "user", "content": user}],
        system=system,
        model="claude",
        temperature=0.1,
        max_tokens=8192,
    )
    # Strip code fences if the LLM forgot and added them anyway.
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:verilog|systemverilog|sv)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip() + "\n"


async def process_one(name: str, spec: str) -> None:
    print(f"\n=== {name}: RTL generate ===")
    rtl = await generate_rtl(spec, name)
    rtl_path = RTL / f"{name}.v"
    rtl_path.write_text(rtl, encoding="utf-8")
    print(f"  rtl → {rtl_path} ({len(rtl)} chars)")

    print(f"=== {name}: review_rtl ===")
    review = await review_code(rtl_code=rtl, file_name=f"{name}.v", model="claude")
    errors   = review.get("error_count", 0)
    warnings = review.get("warning_count", 0)
    print(f"  review → errors={errors} warnings={warnings}")

    review_path = REV / f"{name}.md"
    review_path.write_text(_render_review_md(name, rtl, review), encoding="utf-8")
    print(f"  review → {review_path}")


def _render_review_md(name: str, rtl: str, review: dict) -> str:
    findings = review.get("findings", []) or []
    errs  = [f for f in findings if (f.get("severity") or "").lower() == "error"]
    warns = [f for f in findings if (f.get("severity") or "").lower() == "warning"]

    def fmt(fs):
        if not fs:
            return "_(none)_"
        out = []
        for f in fs:
            out.append(
                f"- **{(f.get('severity') or '?').upper()}** "
                f"[{f.get('category') or f.get('code') or '?'}] "
                f"line {f.get('line', '?')}: {f.get('message', '')}"
            )
        return "\n".join(out)

    return f"""# Phase 4 — `{name}` review

> Auto-generated 2026-05-06. Corresponds to `rtl/{name}.v` and
> `03_module_specs/{name}.md`. This demo skipped the full spec2rtl
> 5-stage pipeline; it used the Stage-4 RTL_GENERATOR prompt directly.

## Artifact
- RTL file: `rtl/{name}.v` ({len(rtl)} characters)

## review_rtl result
| Severity | Count |
|----------|-------|
| ERROR    | {len(errs)} |
| WARNING  | {len(warns)} |

### Errors
{fmt(errs)}

### Warnings
{fmt(warns)}

### review_rtl summary
{review.get('summary', '_(not provided)_')}

### Raw findings JSON
```json
{json.dumps(findings, indent=2, ensure_ascii=False)}
```

---

## Phase 4 exit gate (this module)

- [{'x' if len(errs) == 0 else ' '}] review_rtl reports zero ERROR severity findings
- [x] Manual edits: none yet (auto-generated)
- [ ] User approval: demo auto-approved (no real user sign-off)
"""


async def main():
    for name, spec in SPECS.items():
        await process_one(name, spec)


if __name__ == "__main__":
    asyncio.run(main())
