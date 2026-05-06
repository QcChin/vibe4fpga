# Phase 1 — Requirements

> Copied from `.claude/skills/fpga_flow/phases/01_requirements.md` into the
> user's project at `docs/fpga/01_requirements.md` at the start of Phase 1.
> Every `{{...}}` placeholder must be filled before the exit gate.

## Project name
{{project_name}}

## One-line summary
{{what this design does, in one sentence — e.g. "A UART receiver that
decodes 115200-8-N-1 serial frames from a host and hands bytes to an
AXI-Stream consumer"}}

## Problem statement
{{2-5 sentences: who is this for, what problem it solves, why a custom
FPGA block instead of a CPU + peripheral / off-the-shelf IP}}

## Target platform
| Field | Value |
|-------|-------|
| Device family | {{e.g. Xilinx Artix-7}} |
| Part number   | {{e.g. XC7A35T-1CPG236C}} |
| Board         | {{e.g. Digilent Cmod A7, custom PCB rev B}} |
| Toolchain     | {{Vivado / Quartus / oss-cad-suite / ...}} |
| System clock  | {{e.g. 50 MHz from on-board 50 MHz oscillator}} |

## I/O map (chip-level)
| Pin | Direction | Function | Voltage / std | Notes |
|-----|-----------|----------|---------------|-------|
| {{e.g. P17}} | {{in / out / inout}} | {{e.g. uart_rx_data}} | {{3.3V LVCMOS}} | {{e.g. pulled up externally}} |

(One row per external pin. Even a 3-pin design gets a 3-row table.)

## Functional requirements
Numbered, each testable. Use `FR-N`.

- **FR-1**: {{what must the design do, behaviorally}}
- **FR-2**: {{...}}
- **FR-3**: {{...}}

## Non-functional requirements (measurable targets)
| # | Requirement | Target | How measured |
|---|-------------|--------|--------------|
| NF-1 | System clock | {{e.g. 50 MHz ±100 ppm}} | {{e.g. crystal spec}} |
| NF-2 | Throughput   | {{e.g. ≥ 100 kB/s sustained}} | {{post-sim counter}} |
| NF-3 | Latency      | {{e.g. < 2 µs input-to-output}} | {{timing report + sim}} |
| NF-4 | Utilization  | {{e.g. < 500 LUTs, < 300 FFs}} | {{post-synth utilization report}} |
| NF-5 | Power        | {{e.g. < 150 mW static}} | {{Vivado power report}} |

## Reset + clocking
- Reset source: {{e.g. external pushbutton via debouncer, power-on, soft}}
- Reset polarity: {{active-high / active-low}}
- Reset type: {{synchronous / asynchronous-assert-synchronous-deassert}}
- Clock domains: {{list them, or "single domain (sys_clk 50 MHz)"}}

## External protocols / interfaces referenced
For each protocol (UART, SPI, I²C, AXI, PCIe, DDR, HDMI, etc.), point to
the authoritative spec/datasheet that governs the implementation. This is
what Phase 3 (module spec) will cite.

- {{protocol name}} → {{spec document, revision, specific sections}}

## Out of scope
Explicit non-goals. Be specific — "no DMA" is better than "minimal features".

- {{item}}
- {{item}}

## Assumptions
Constraints we're taking as given without proving them here.

- {{item, e.g. "the host always sends one byte at a time; no back-to-back streaming"}}

## Success criteria (one-line each)
What does "done" look like for stages 6-9?

- **Sim**: {{e.g. 100% of directed tests pass, line coverage ≥ 95%}}
- **Synth**: {{e.g. zero errors, zero critical warnings, meets NF-1}}
- **Timing**: {{e.g. WNS ≥ 0 ns at {{NF-1}} clock}}
- **Bring-up**: {{e.g. scope capture matches sim within ±2 ns}}

---

## Phase 1 exit gate

- [ ] Problem statement unambiguous and agreed
- [ ] Target platform + part number confirmed
- [ ] Every functional requirement is testable (not "should be fast")
- [ ] Every non-functional requirement has a measurable target + method
- [ ] I/O map covers every external pin
- [ ] Reset + clocking strategy decided
- [ ] Referenced protocols have authoritative doc pointers
- [ ] Scope is explicit: functional reqs + non-goals + assumptions
- [ ] User approval: reply **"approve phase 1"** or tick this box

Once all ticked, proceed to `docs/fpga/02_architecture.md`.
