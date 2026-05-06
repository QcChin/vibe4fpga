---
name: fpga_flow
description: Drive an FPGA project through a disciplined 6-phase workflow (requirements → architecture → module spec → RTL → verification → closure) with per-phase docs, user sign-off gates, and auto-dispatch to the vibe4fpga MCPs. Activates when the user describes an FPGA/HDL design intent or works in a project with Verilog/SystemVerilog/VHDL files.
trigger: fpga|verilog|systemverilog|vhdl|rtl|hdl|synthesis|asic|xilinx|intel|altera|lattice|microchip|clock domain|fsm|state machine|pipeline
---

# fpga_flow

Drive an FPGA project through a disciplined, 6-phase development workflow with
per-phase documentation, user sign-off gates, and auto-dispatch to the
vibe4fpga MCPs when tool calls are appropriate.

This skill is **hand-maintained** (not generated from `skill.yaml`). It exists
to bridge a gap the MCPs alone don't fill: the MCPs automate stages 5-9 of
FPGA development (RTL gen / review / sim / synth / bring-up), but stages 1-4
(requirements / architecture / microarch / interfaces) are just structured
conversations with a human. This skill encodes that structure.

## When to activate

Activate automatically when any of the following is true in the user's opening
message(s) about a project:

- They mention Verilog / SystemVerilog / VHDL, FPGA, ASIC, RTL, HDL, or
  synthesis
- They reference a target part family (Xilinx / Intel / Lattice / Microchip / etc.)
- They describe a digital hardware design intent (counter, UART, SPI, AXI,
  DMA, FIFO, CDC, state machine, etc.)
- The working directory contains `.v` / `.sv` / `.vhd` / `.xdc` / `.qsf` /
  `.pcf` / `.lpf` files
- They invoke the skill explicitly: "use fpga_flow", "let's plan this FPGA
  project", "walk me through the FPGA development"

**Do NOT activate** for pure software tasks, even if they mention silicon
(e.g., writing a Linux driver for an existing FPGA — that's software).

## The 6 phases

```
┌─ 1. Requirements  ─── WHAT & WHY ─────────────────────┐
│  docs/fpga/01_requirements.md                          │
│  Output: problem statement, targets, I/O map           │
│  Gate: user confirms scope is complete & measurable    │
├─ 2. Architecture  ─── SYSTEM SHAPE ────────────────────┤
│  docs/fpga/02_architecture.md                          │
│  Output: block diagram (ASCII/Mermaid), clock domains, │
│          data flow, top-level module list              │
│  Gate: user confirms decomposition is sound            │
├─ 3. Module spec   ─── PER-BLOCK CONTRACT ──────────────┤
│  docs/fpga/03_module_specs/<name>.md  (one per module) │
│  Output: port table, FSM, pipeline stages, edge cases  │
│  MCPs:   search_protocol / search_datasheet for refs   │
│  Gate: all module specs approved → RTL phase unlocked  │
├─ 4. RTL           ─── CODE + FIRST-ORDER QA ───────────┤
│  rtl/<name>.v  +  docs/fpga/04_rtl_review/<name>.md    │
│  MCPs:   spec_to_rtl → review_rtl (per module)         │
│  Gate: every module has zero ERROR findings in review  │
├─ 5. Verification  ─── COVERAGE + WAVEFORM DEBUG ───────┤
│  tb/<name>_tb.sv  +  docs/fpga/05_verification/*.md    │
│  MCPs:   generate_testbench → run_simulation           │
│          → debug_waveform → score_verification         │
│  Gate: sim pass + score_verification overall ≥ PASS    │
├─ 6. Closure       ─── SYNTH + TIMING + (opt) BOARD ────┤
│  docs/fpga/06_closure/*.md                             │
│  MCPs:   run_synthesis / compile_quartus_project       │
│          → get_timing_report → suggest_timing_fix      │
│          (optional: analyze_instrument_diff on board)  │
│  Gate: timing WNS ≥ 0 at target clock, no synth errors │
└────────────────────────────────────────────────────────┘
```

## How to drive the flow

### Phase detection

On entering the conversation for an FPGA project, check for `docs/fpga/` in
the working directory:

- **No `docs/fpga/`** → you're entering Phase 1. Announce the flow briefly
  and ask the user for a short problem statement.
- **`docs/fpga/01_requirements.md` exists** → check its exit-gate checklist.
  If all items checked, advance; if not, resume Phase 1 and fill gaps.
- Walk the phase chain the same way for `02_…`, `03_…`, etc.

Do NOT skip phases silently. If the user wants to skip (e.g., "this is a
small utility, skip architecture"), ask explicitly and record the skip +
rationale in a `docs/fpga/SKIPPED.md`.

### Within a phase

1. Copy the corresponding template from
   `.claude/skills/fpga_flow/phases/NN_phase.md` into
   `docs/fpga/NN_phase.md` in the user's project (if it doesn't exist).
2. Read any existing content and identify the `{{fill-me}}` placeholders
   or empty sections.
3. Ask the user targeted questions to fill the gaps. One cluster of
   questions at a time — don't dump 20 questions at once.
4. As answers come in, edit the doc in place. After each edit, show the
   user what you wrote (a brief diff summary is fine, don't re-dump the
   whole file).
5. When every template section is filled, present the exit-gate checklist
   and ask the user to confirm each item.
6. **Wait for explicit approval** ("approved", "go to next phase", or the
   user ticking the gate checklist). Do NOT advance just because the doc
   looks full.

### Calling MCPs within phases

Phases 3-6 have automated steps. When you reach them:

- **Phase 3 (module spec)**: optionally call `search_protocol` /
  `search_datasheet` (datasheet-mcp) when the user names a protocol you'd
  like a reference for. Paste relevant quotes into the module spec.
- **Phase 4 (RTL)**: for each approved module spec, call
  `spec_to_rtl` with the spec as input and `project_path` set to the user's
  project root (so Stage 3 context injection picks up naming conventions).
  Write output to `rtl/<name>.v`. Immediately run `review_rtl` on the
  result; record findings in `docs/fpga/04_rtl_review/<name>.md`. If
  findings contain any `severity: ERROR` entries, do NOT advance — go back
  to the module spec or fix manually, then re-run.
- **Phase 5 (verification)**: for each module, call `generate_testbench`
  (verify-mcp) with the RTL + spec + coverage goals. Save to
  `tb/<name>_tb.sv`. Then `run_simulation` (eda-bridge-mcp) with
  `simulator: "icarus"`. Capture the VCD and call `debug_waveform`
  (waveform-mcp) on it. Finally call `score_verification` (verify-mcp)
  to get an overall PASS / REVIEW / FAIL verdict.
- **Phase 6 (closure)**: call `run_synthesis` (eda-bridge-mcp) or
  `compile_quartus_project` (quartus-mcp) per the user's toolchain choice.
  On timing violations, call `suggest_timing_fix` (fpga-project-mcp) with
  the timing report. For optional bring-up, use
  `analyze_instrument_diff` (instrument-mcp) to compare scope captures vs
  simulation.

If a required tool (iverilog, vivado, quartus_sh, yosys) isn't on the
user's PATH, surface a clear "missing tool X — install via Y" message
rather than failing silently. Each MCP reports this as structured output.

## Constraints

These are hard rules. Never break them.

1. **Never edit RTL before the corresponding module spec (Phase 3) is
   approved.** The whole point is that the spec anchors the code.
2. **Never run synthesis before verification (Phase 5) shows a PASS or
   REVIEW verdict.** Synth-failures on un-verified RTL waste the user's
   time and obscure real bugs.
3. **Never auto-advance phases.** Every phase exit requires an explicit
   "approved" from the user, even if the doc appears complete.
4. **Never silently drop a module from Phase 3.** If the architecture in
   Phase 2 lists 4 blocks, Phase 3 produces 4 specs — or the user
   explicitly says "merge X into Y" or "drop Z", recorded in the phase 2
   doc.
5. **Every approved phase has a dated commit hint.** When closing a
   phase, tell the user "this is a good point to `git commit -am 'phase N:
   <one-line summary>'`" but do NOT commit on their behalf unless they
   explicitly ask.
6. **Keep artifacts under version control.** All `docs/fpga/` and `rtl/` /
   `tb/` outputs should be committable. Avoid scratch-only artifacts that
   can't be reviewed after the session.

## Artifact layout in the user's project

```
<user-project>/
├── docs/
│   └── fpga/
│       ├── 01_requirements.md
│       ├── 02_architecture.md
│       ├── 03_module_specs/
│       │   ├── <module_a>.md
│       │   └── <module_b>.md
│       ├── 04_rtl_review/
│       │   ├── <module_a>.md
│       │   └── <module_b>.md
│       ├── 05_verification/
│       │   ├── plan.md
│       │   └── results.md
│       └── 06_closure/
│           ├── synthesis.md
│           └── timing.md
├── rtl/
│   ├── <module_a>.v
│   └── <module_b>.v
├── tb/
│   ├── <module_a>_tb.sv
│   └── <module_b>_tb.sv
└── constraints/
    └── top.xdc   (or .pcf / .qsf depending on toolchain)
```

## Resuming a session

When the user returns to an in-progress flow:

1. Read `docs/fpga/` to discover the highest-numbered completed phase.
2. Summarize progress in one paragraph ("You're at phase 4: RTL. 2/3
   modules have been generated and reviewed without errors; 1 module
   (uart_rx_fsm) is still pending.")
3. Ask what they want to work on next. Don't assume.

## When NOT to follow the flow

- **Small bug fixes** on existing code: don't trigger a phase workflow for
  a one-line Verilog fix. Just fix it.
- **Non-design questions** ("what does this module do?", "how do I run the
  sim?"): answer directly, no flow activation.
- **User explicitly opts out**: "skip the phases, just generate the RTL" →
  obey, but warn once that you're bypassing the gate discipline.
