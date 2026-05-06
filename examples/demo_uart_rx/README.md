# demo_uart_rx

End-to-end reference demo of the `fpga_flow` skill applied to a small
but realistic FPGA design: a UART 8-N-1 receiver at 115200 baud on a
50 MHz clock.

## What this demo shows

- How the 6-phase `fpga_flow` workflow decomposes an FPGA project from
  requirements down to bitstream-ready RTL
- How each phase produces a reviewable document under `docs/fpga/`
- How the vibe4fpga MCPs (`spec_to_rtl`, `review_rtl`,
  `generate_testbench`) slot in at phases 4 and 5 to automate the
  code-shaped work
- Where the flow stops when its toolchain prerequisites are missing
  (phase 5 sim, phase 6 synth)

## Artifact map

```
demo_uart_rx/
├── docs/fpga/
│   ├── 01_requirements.md              ← filled from template
│   ├── 02_architecture.md              ← block diagram + clock domains
│   ├── 03_module_specs/
│   │   ├── uart_baud_gen.md            ← port table, behavior, coverage hints
│   │   └── uart_rx.md                  ← FSM with 11 states, 7 coverage points
│   ├── 04_rtl_review/
│   │   ├── uart_baud_gen.md            ← review_rtl findings
│   │   └── uart_rx.md                  ← review_rtl findings
│   ├── 05_verification/
│   │   └── plan.md                     ← tb files, deferred sim
│   └── 06_closure/
│       └── DEFERRED.md                 ← yosys/nextpnr install needed
├── rtl/
│   ├── uart_baud_gen.v                 ← 60 lines, generated + reviewed
│   └── uart_rx.v                       ← 244 lines, generated + reviewed
├── tb/
│   ├── uart_baud_gen_tb.sv             ← 18 KB testbench, 3 coverage points
│   └── uart_rx_tb.sv                   ← 18 KB testbench, 5 coverage points, 6 SVA
├── sim/                                 ← (empty; populated when iverilog runs)
└── .run_spec2rtl.py                    ← the driver script that produced
                                          rtl/ + 04_rtl_review/. Kept as a
                                          reference for how to invoke the
                                          skill library directly.
```

## How the artifacts were produced

| Step | How | LLM calls |
|------|-----|-----------|
| Phase 1-3 docs | Claude Code drove the conversation per SKILL.md; artifacts written under `docs/fpga/` | 0 |
| `rtl/*.v`      | `.run_spec2rtl.py` called `fpga_project_mcp.skills._llm.call_llm` with the `RTL_GENERATOR_SYSTEM` prompt directly — bypassing the full spec2rtl pipeline (which has a couple of pydantic/JSON brittleness points flagged during the run). User's Anthropic proxy (ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN) was used end-to-end. | 2 |
| `04_rtl_review/*.md` | Same driver called `fpga_project_mcp.skills.code_review.checker.review_code` per generated RTL | 2 |
| `tb/*.sv`      | Separate driver called `verify_mcp.skills.testbench_gen.skill.run` | 2 |
| Phase 5 sim    | **skipped** — iverilog not installed |
| Phase 6 synth  | **skipped** — yosys/nextpnr not installed |

Total LLM calls to the user's proxy: **6**.

## Known issues / follow-ups noted during the run

- `spec2rtl` pipeline's `DesignIntent.state_machine` was typed as `dict |
  None`; LLM Stage 1 sometimes returns a string FSM description for
  complex FSMs. **Relaxed to `dict | str | None`** during the demo
  (see `packages/mcp-servers/fpga-project-mcp/src/fpga_project_mcp/skills/spec2rtl/models.py`).

- `spec2rtl` pipeline Stage 5 (self-check) occasionally receives
  non-JSON LLM output and crashes `json.loads`. **Not fixed yet** —
  future work. For this demo we bypassed the pipeline and used
  the Stage 4 prompt directly, which doesn't rely on structured
  JSON intermediates.

- 4 LLM-using MCPs (`fpga-project-mcp`, `waveform-mcp`,
  `instrument-mcp`, `verify-mcp`) had `vibe4fpga-llm-client>=0.1.0`
  as a plain dep — missing the `[claude]` extra. After `uv tool
  install --force`, `anthropic` SDK was absent. **Fixed** by
  changing dep to `vibe4fpga-llm-client[claude]>=0.1.0` in each
  pyproject (root cause fix for future `uv tool install` runs).
  The currently-running tool venvs were patched manually via
  `uv pip install --python <venv> anthropic>=0.25.0`.

- `review_rtl` flagged one `ERROR` per module on out-of-contract
  parameter corner cases (DIV=1 for baud_gen, OVERSAMPLE ≤ 2 for
  uart_rx). Both cases are outside the spec's declared parameter
  ranges. Classified as **accepted deviations**, not real bugs.

## How to finish the demo

1. Install oss-cad-suite (one download, gives you yosys, nextpnr,
   iverilog, and more). Add its `bin/` to PATH.
2. Run `mcp__eda-bridge-mcp__run_simulation` on each `tb/*.sv` — this
   populates `sim/*.vcd` and updates `05_verification/`.
3. Run `mcp__yosys-mcp__synthesize_ice40` then `pnr_ice40` then
   `pack_ice40_bitstream` for the iCE40 target listed in
   `01_requirements.md` §Target platform. Results land in
   `06_closure/`.
4. Everything is then ready for a real iCEstick / BlackIce deploy.

## Regenerating the artifacts

```bash
# From repo root:
cd packages/mcp-servers/fpga-project-mcp
uv run python ../../../examples/demo_uart_rx/.run_spec2rtl.py

# For testbenches (from verify-mcp dir):
cd packages/mcp-servers/verify-mcp
# ... call generate_testbench as in the earlier conversation
```
