# Phase 6 — Closure

> Files produced:
> - `docs/fpga/06_closure/synthesis.md` — synth + utilization
> - `docs/fpga/06_closure/timing.md`    — STA + any fix work
> - `docs/fpga/06_closure/bringup.md`   — optional, only if board-level

## Source of truth
- `docs/fpga/01_requirements.md` §Non-functional requirements (NF-1 clock,
  NF-4 utilization, NF-5 power)
- `docs/fpga/01_requirements.md` §Success criteria §Synth / §Timing / §Bring-up
- All `rtl/*.v` files that made it through Phase 5

Phase 6 is where the design meets the silicon. Every NF-* target from
Phase 1 must be measured and reported here.

---

## 6a. Synthesis

### Toolchain
| Field | Value |
|-------|-------|
| Vendor        | {{Xilinx / Intel / Lattice / ...}} |
| Tool          | {{Vivado 2024.2 / Quartus Prime Lite 23.1 / Yosys + nextpnr}} |
| Target part   | {{from Phase 1 §Target platform}} |
| Top module    | {{usually from Phase 2 §Top-level module list}} |
| Constraints   | `constraints/{{top}}.xdc` (or `.qsf` / `.pcf` / `.lpf`) |

### Invocation
Choose one path based on the toolchain:

**Vivado flow** (via `eda-bridge-mcp.run_synthesis`):
```
files       = [rtl/*.v]
top_module  = "{{top}}"
part        = "{{part}}"
```

**Quartus flow** (via `quartus-mcp`):
```
create_qsf  → add_hdl_file × N → assign_pin × N
            → compile_quartus_project → get_timing_report
```

**Yosys flow** (open-source):
```
synthesize_{ice40 | ecp5 | generic}  → pnr_{ice40 | ecp5}
            → pack_ice40_bitstream
```

### Results

| Check | Result |
|-------|--------|
| Success | ✓ / ✗ |
| Errors   | {{N — list below if nonzero}} |
| Warnings | {{N}} |
| Critical warnings | {{N}} |

### Utilization (measure against NF-4)
| Resource | Used | Available | % | NF-4 target | Meets? |
|----------|------|-----------|---|-------------|--------|
| LUT      | {{...}} | {{...}} | {{%}} | {{...}} | ✓ / ✗ |
| FF       | {{...}} | {{...}} | {{%}} | {{...}} | ✓ / ✗ |
| BRAM     | {{...}} | {{...}} | {{%}} | — | — |
| DSP      | {{...}} | {{...}} | {{%}} | — | — |
| Power (est.) | {{mW}} | — | — | NF-5: {{...}} | ✓ / ✗ |

### Error / critical-warning triage
If any: paste the classified output from
`eda-bridge-mcp.classify_errors` and note resolution per entry.

- **{{code}}**: {{description}}
  - Category: {{...}}
  - Suggestion: {{...}}
  - Resolution: {{fixed by X / dismissed because Y / deferred}}

### 6a exit gate
- [ ] Synth success, zero errors
- [ ] Critical warnings explained or dismissed
- [ ] Every NF-4 / NF-5 target measured and reported (✓ or ✗)
- [ ] User approval: **"approve synthesis"**

---

## 6b. Timing closure (STA)

### Target
- Clock spec from NF-1: {{e.g. 50 MHz, period 20 ns}}
- Other clocks (if any): {{list}}

### Raw report
Call: `{{eda-bridge-mcp.get_timing_report / quartus-mcp.get_timing_report}}`

| Metric | Value | Meets target? |
|--------|-------|---------------|
| WNS (Worst Negative Slack) | {{ns}} | ≥ 0 → ✓ |
| TNS (Total Negative Slack) | {{ns}} | 0 → ✓ |
| Fmax achieved | {{MHz}} | ≥ NF-1 → ✓ |

### Violations (if any)
If WNS < 0, list the N worst paths:

| # | Startpoint | Endpoint | Slack | Logic levels |
|---|------------|----------|-------|--------------|
| 1 | {{...}}    | {{...}}  | {{ns}} | {{N}} |

### Fixes applied
When WNS < 0, call `fpga-project-mcp.suggest_timing_fix` and record the
recommended strategy:

- **Strategy**: pipeline register / multi-cycle path / logic
  restructuring
- **Target path**: {{...}}
- **RTL / XDC change**: {{snippet or diff reference}}
- **Rerun result**: WNS now {{...}} ns

Iterate until WNS ≥ 0.

### 6b exit gate
- [ ] WNS ≥ 0 ns at the target clock
- [ ] TNS = 0
- [ ] Fmax meets NF-1
- [ ] All applied fixes are documented with before/after slack
- [ ] No multi-cycle / false paths introduced without justification
- [ ] User approval: **"approve timing"**

---

## 6c. Bring-up (OPTIONAL)

Only if the user is bringing this up on physical hardware. Skip the
section entirely with a one-line "deferred — no board available" if not.

### Programming
| Field | Value |
|-------|-------|
| Bitstream file | {{*.bit / *.sof / *.bin}} |
| Programmer     | {{JTAG / USB-Blaster / iceprog / ...}} |
| Result         | ✓ configured / ✗ + error |

### Instrument comparison
For each critical signal you captured on a scope / LA:

- **Signal**: {{pin name}}
- **Capture**: {{CSV path}}
- **Simulation reference**: {{VCD path, signal name}}
- **`instrument-mcp.analyze_instrument_diff` verdict**: {{category}}
  ({{expected / suspicious / anomalous}})
- **Resolution**: {{expected drift / real bug — fixed by Z}}

### 6c exit gate (if in scope)
- [ ] Bitstream programs successfully
- [ ] Critical signals captured on scope
- [ ] Every diff classified; none `anomalous` without resolution
- [ ] User approval: **"approve bring-up"**

---

## Final project gate

- [ ] 6a (synthesis) approved
- [ ] 6b (timing) approved
- [ ] 6c (bring-up) approved OR explicitly deferred
- [ ] All Phase 1 success criteria measured and met
- [ ] Final handoff artifact written: `docs/fpga/RELEASE.md` with a
      one-page summary linking every prior phase doc
- [ ] User approval: **"release"**

When all ticked, this project is done. `git tag` is a reasonable next
move.
