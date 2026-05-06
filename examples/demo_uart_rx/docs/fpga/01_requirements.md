# Phase 1 — Requirements

> Populated 2026-05-05 by the `fpga_flow` skill. Template:
> `.claude/skills/fpga_flow/phases/01_requirements.md`.
> **Status: approved for phase 2.**

## Project name
demo_uart_rx

## One-line summary
A UART receiver that decodes standard 8-N-1 serial frames at 115200 baud
from a single external RX pin and delivers one received byte per frame to
a downstream consumer using a simple valid/ready handshake.

## Problem statement
This demo exists to prove out the `fpga_flow` skill end-to-end on a
small, well-understood peripheral. UART 8-N-1 is ubiquitous (every
embedded board has it), its spec is unambiguous, it exercises interesting
behavior (start-bit detection, 16× oversampling, framing errors), but is
small enough that the whole 6-phase flow fits in a short session.
Reference material: FPGA4Fun's "Serial interface — Async" page, which is
freely available online.

## Target platform
| Field | Value |
|-------|-------|
| Device family | Lattice iCE40 (open-source toolchain friendly) |
| Part number   | iCE40HX8K-CT256 (or any HX/LP variant) |
| Board         | iCEstick / Lattice BlackIce / generic |
| Toolchain     | Yosys + nextpnr-ice40 + icepack (open-source) |
| System clock  | 50 MHz (typical on-board oscillator) |

Chosen iCE40 because the vibe4fpga `yosys-mcp` already has a full flow
for it, so Phase 6 can run without vendor IDE installs. The design is
vendor-agnostic Verilog so it ports to Xilinx/Intel without changes.

## I/O map (chip-level)
| Pin | Direction | Function | Voltage / std | Notes |
|-----|-----------|----------|---------------|-------|
| CLK       | in  | 50 MHz system clock   | 3.3 V LVCMOS | from on-board crystal |
| RST_N     | in  | active-low reset      | 3.3 V LVCMOS | external pushbutton through debouncer (out of scope) |
| UART_RX   | in  | serial data in        | 3.3 V LVCMOS | idle = high |
| RX_DATA[7:0] | out | received byte       | internal     | to downstream consumer |
| RX_VALID  | out | data valid for 1 cycle | internal   | handshake |
| RX_READY  | in  | consumer ready        | internal     | handshake |
| RX_FRAME_ERROR | out | missing stop bit  | internal     | asserted 1 cycle per bad frame |

External pins are just CLK / RST_N / UART_RX; the rest is internal to
the FPGA fabric.

## Functional requirements
- **FR-1**: Decode standard 8-N-1 UART frames: 1 start bit (low), 8 data
  bits LSB-first, 1 stop bit (high). No parity.
- **FR-2**: Operate at 115200 baud with 16× oversampling (baud_gen must
  produce an enable pulse at 115200 × 16 = 1.8432 MHz).
- **FR-3**: Sample each bit at the center of its bit time (sample at
  oversampling tick 8 out of 16).
- **FR-4**: Present the received byte on RX_DATA with RX_VALID asserted
  for exactly one sys_clk cycle when the stop bit is valid.
- **FR-5**: If the stop bit is low (framing error), assert
  RX_FRAME_ERROR for exactly one sys_clk cycle and do not assert
  RX_VALID; discard the byte.
- **FR-6**: Return to IDLE after every frame (good or bad) and
  immediately be ready to detect the next start bit.
- **FR-7**: On reset (RST_N = 0), drive all outputs to deasserted state
  (RX_VALID = 0, RX_FRAME_ERROR = 0, RX_DATA = 8'h00), return FSM to
  IDLE.

## Non-functional requirements (measurable targets)
| # | Requirement | Target | How measured |
|---|-------------|--------|--------------|
| NF-1 | System clock     | 50 MHz ±100 ppm     | crystal spec |
| NF-2 | Baud rate error  | < 2% of 115200      | derived from clock division ratio |
| NF-3 | Latency          | < 87 µs (≈1 bit time) from stop bit to RX_VALID | simulation |
| NF-4 | Utilization      | < 80 LUTs, < 40 FFs | post-synth report (Yosys) |
| NF-5 | Fmax             | ≥ 80 MHz            | post-synth STA, so 50 MHz has margin |

## Reset + clocking
- Reset source: external RST_N pin (debouncing is out of scope)
- Reset polarity: **active-low**
- Reset type: **synchronous** (sampled on posedge clk)
- Clock domains: **single domain only** — sys_clk (50 MHz)

No CDC issues. The UART_RX pin is technically asynchronous but the
design uses a 2-FF synchronizer before the FSM sees it.

## External protocols / interfaces referenced
- **UART 8-N-1** → Classic serial format, no single canonical standard
  doc. Behavioral reference: FPGA4Fun "Serial interface — Async"
  (https://www.fpga4fun.com/SerialInterface.html). Not strictly
  required for this small design but noted for completeness.

## Out of scope
- TX (transmit) path — this is RX only
- Parity, 7-bit data, 2 stop bits, other baud rates — pure 8-N-1 115200
- Flow control (RTS/CTS)
- FIFO buffering (single-byte output is enough for the demo)
- Configurable baud rate — compile-time parameter only
- Debouncing of the external RST_N pin

## Assumptions
- The upstream transmitter is within ±2% of 115200 baud (industry norm)
- The host de-asserts RX_READY within one byte time so the single-byte
  "buffer" (the RX_DATA output register) doesn't overflow
- Power-on reset is asserted long enough (~ 10 cycles) to be cleanly
  synchronized — handled externally

## Success criteria
- **Sim**: all directed tests pass (clean frame, framing error,
  back-to-back frames, reset during active receive)
- **Synth**: zero errors from `yosys-mcp.synthesize_ice40`
- **Timing**: WNS ≥ 0 ns at 50 MHz from `yosys-mcp.pnr_ice40`
- **Bring-up**: deferred (no physical board in the demo)

---

## Phase 1 exit gate

- [x] Problem statement unambiguous and agreed
- [x] Target platform + part number confirmed (iCE40HX8K-CT256)
- [x] Every functional requirement is testable
- [x] Every non-functional requirement has a measurable target + method
- [x] I/O map covers every external pin (3: CLK, RST_N, UART_RX)
- [x] Reset + clocking strategy decided (sync, active-low, single domain)
- [x] Referenced protocols have doc pointers (FPGA4Fun)
- [x] Scope explicit (in-scope + out-of-scope + assumptions)
- [x] **User approval: approved phase 1 (demo auto-approval)**

Proceed to `02_architecture.md`.
