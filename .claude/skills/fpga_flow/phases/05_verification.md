# Phase 5 — Verification

> Two files produced:
> - `docs/fpga/05_verification/plan.md`  (project-level plan, this template)
> - `docs/fpga/05_verification/<module_name>.md`  (per-module results)
>
> Unlike Phase 3/4 which are per-module, Phase 5 has a **project-level
> plan** first, then per-module execution.

## Source of truth
- All `docs/fpga/03_module_specs/*.md` §Verification hints (coverage points)
- All `docs/fpga/04_rtl_review/*.md` §Warnings (extra directed tests)
- `docs/fpga/01_requirements.md` §Success criteria §Sim bullet

## Verification plan (this file)

### Strategy
{{One paragraph: which simulator, which tb style, coverage goals. E.g.
"Icarus Verilog for unit tests; directed SV testbench per module with
SVA assertions and a covergroup; 95% line coverage is the minimum bar;
cross-coverage between reset-recovery and all other covergroups is the
acceptance gate."}}

### Toolchain
| Stage | Tool | Expected on PATH |
|-------|------|------------------|
| Unit sim | iverilog | `iverilog` + `vvp` |
| Tb gen  | `verify-mcp.generate_testbench` | (already installed) |
| Debug   | `waveform-mcp.debug_waveform` | (already installed) |
| Score   | `verify-mcp.score_verification` | (already installed) |

If a tool isn't found, the corresponding MCP will return a
`ToolNotFoundError` — don't mask it, report it to the user with the
install hint.

### Modules in scope
| Module | Sim driver file | VCD output | Coverage target |
|--------|-----------------|------------|-----------------|
| {{module_a}} | `tb/{{module_a}}_tb.sv` | `sim/{{module_a}}.vcd` | {{e.g. 95% line, 100% FSM arc}} |
| {{module_b}} | `tb/{{module_b}}_tb.sv` | `sim/{{module_b}}.vcd` | {{...}} |

### Integration tests
Do we also simulate modules together at a higher level?
- {{e.g. "uart_rx_top_tb.sv wires all 4 modules and drives a full frame
  from the external pin"}}
- {{or "no — unit-level coverage sufficient because there's no
  module-to-module handshake complexity"}}

### Exit criteria (for Phase 5 overall)
- Every module's sim returns PASS (no `$error`, no assertion failure)
- `score_verification` overall verdict for every module: PASS or REVIEW
- Zero unexplained `waveform_debug` findings of severity ERROR
- Coverage targets (above) met or REVIEW-flagged with rationale

---

## Per-module template (copy to `05_verification/<name>.md`)

Use the template below for each module. The skill auto-populates most
fields after running the MCP calls.

---

## {{module_name}} — verification result

### Inputs
- Spec: `docs/fpga/03_module_specs/{{module_name}}.md`
- RTL: `rtl/{{module_name}}.v` (at SHA {{...}})
- Review: `docs/fpga/04_rtl_review/{{module_name}}.md`

### Testbench generation
Call: `verify-mcp.generate_testbench`
- Coverage goals passed: {{list from spec §Verification hints}}
- Simulator: iverilog
- Output: `tb/{{module_name}}_tb.sv`
- Assertions: {{N SVA + N covergroup bins}}

### Simulation run
Call: `eda-bridge-mcp.run_simulation`
| Field | Value |
|-------|-------|
| Simulator     | iverilog |
| Source files  | {{list}} |
| Duration      | {{sim time / wall time}} |
| Verdict       | PASS / FAIL / ERROR / UNCLEAR |
| Pass count    | {{N}} |
| Fail count    | {{N}} |
| Assertions PASSED / FAILED | {{A}} / {{B}} |
| VCD output    | `sim/{{module_name}}.vcd` |

If verdict is not PASS, paste the relevant `error_lines` from the
simulator log and describe the root cause before continuing.

### Waveform debug
Call: `waveform-mcp.debug_waveform` (or `waveform-mcp-rs` for faster
parse on large VCDs)
- Anomaly count: {{N}} (ERROR: {{N}}, WARNING: {{N}})
- Top 3 anomalies (if any) with root cause:
  1. {{type}} on `{{signal}}` at {{time}} ns — {{root cause}}
  2. ...

### Score
Call: `verify-mcp.score_verification`
| Stage | Verdict | Score |
|-------|---------|-------|
| Lint  | {{...}} | {{...}} |
| Sim   | {{...}} | {{...}} |
| Formal| {{skipped / ...}} | — |
| Synth | {{deferred to Phase 6}} | — |
| Spec  | {{...}} | {{...}} |
| **Overall** | **PASS / REVIEW / FAIL** | {{0-100}} |

### Coverage summary
- Line: {{%}}
- FSM arc: {{%}}
- Covergroup bins hit: {{hit}}/{{total}}

### Phase 5 exit gate (per module)

- [ ] Testbench compiled without errors
- [ ] Sim verdict = PASS
- [ ] Every spec coverage point has at least one hit
- [ ] No unexplained ERROR anomalies from `debug_waveform`
- [ ] `score_verification` overall = PASS or REVIEW (with reviewed
      justification)
- [ ] User approval: reply **"approve verification for {{module_name}}"**
      or tick

---

## Phase 5 exit gate (project-wide)

- [ ] Every in-scope module has an approved per-module result doc
- [ ] Integration tests (if any) also PASS
- [ ] All exit-criteria from §Exit criteria met
- [ ] User approval: reply **"approve phase 5"** or tick

Once approved, Phase 6 (closure) unlocks.
