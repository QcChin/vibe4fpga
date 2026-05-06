# fpga_flow skill (dev doc)

> **This directory is hand-maintained.** It is not written by
> `tools/gen-skills/generate.py`. The generator only writes skills
> declared in a `packages/mcp-servers/*/skill.yaml` — `fpga_flow` is
> not one of those. The generator is additive; it will leave this
> directory untouched.

## Purpose

The 9 MCPs in this monorepo cover stages 5-9 of FPGA development:

| Stage | MCP |
|-------|-----|
| RTL gen / review | `fpga-project-mcp` (spec2rtl, review_rtl) |
| Testbench + sim  | `verify-mcp`, `eda-bridge-mcp` |
| Waveform debug   | `waveform-mcp`, `waveform-mcp-rs` |
| Synth + STA      | `eda-bridge-mcp`, `quartus-mcp`, `yosys-mcp` |
| Timing fix       | `fpga-project-mcp` (suggest_timing_fix) |
| Scope comparison | `instrument-mcp` |

Stages 1-4 — requirements, architecture, microarch, interfaces — aren't
automated by any MCP. They're structured conversations between the
engineer and Claude Code. This skill encodes that structure so no phase
is skipped, every phase produces a reviewable artifact, and MCP calls
fire at the right time.

## Files in this directory

| Path | Role |
|------|------|
| `SKILL.md` | Orchestrator prompt read by Claude Code. Frontmatter + triggers + phase-by-phase instructions + hard constraints. |
| `phases/01_requirements.md` | Phase 1 template (copied into user project) |
| `phases/02_architecture.md` | Phase 2 template |
| `phases/03_module_spec.md`  | Phase 3 template (one copy per module) |
| `phases/04_rtl.md`          | Phase 4 template (one copy per module) |
| `phases/05_verification.md` | Phase 5 template (plan + per-module results) |
| `phases/06_closure.md`      | Phase 6 template (synth + timing + bring-up) |
| `README.md` | This file — dev-facing doc. Skip on session load. |

## How it gets invoked

1. **Auto-trigger**: Claude Code scans the user's conversation against
   the trigger regex in `SKILL.md` frontmatter
   (`fpga|verilog|rtl|hdl|...`). When it matches, Claude reads the
   skill and starts driving the 6-phase flow.
2. **Explicit**: the user types `/fpga_flow` in Claude Code, or
   "use the fpga_flow skill", etc.
3. **Resumption**: Claude detects `docs/fpga/` in the working directory
   and finds the highest-numbered phase already started.

## What the skill produces in the user's project

```
<user-project>/
├── docs/fpga/
│   ├── 01_requirements.md          (Phase 1)
│   ├── 02_architecture.md          (Phase 2)
│   ├── 03_module_specs/<N>.md      (Phase 3, one per module)
│   ├── 04_rtl_review/<N>.md        (Phase 4, one per module)
│   ├── 05_verification/
│   │   ├── plan.md                 (Phase 5, project-wide)
│   │   └── <N>.md                  (Phase 5, one per module)
│   ├── 06_closure/
│   │   ├── synthesis.md            (Phase 6a)
│   │   ├── timing.md               (Phase 6b)
│   │   └── bringup.md              (Phase 6c, optional)
│   └── RELEASE.md                  (one-page handoff, final gate)
├── rtl/<N>.v                       (Phase 4 output)
├── tb/<N>_tb.sv                    (Phase 5 output)
├── sim/<N>.vcd                     (Phase 5 output)
└── constraints/top.{xdc,qsf,pcf,lpf} (Phase 6a input)
```

## How to modify

- **Add a new phase** → add the template under `phases/`, update the
  phase chain section in `SKILL.md`, update this README.
- **Change which MCP a phase calls** → edit the "Calling MCPs within
  phases" section of `SKILL.md`. Keep the mapping in sync with the
  per-phase template's invocation notes.
- **Tighten a constraint** → edit the "Constraints" section of
  `SKILL.md`. These are hard rules the skill pledges to follow; don't
  add rules it can't actually enforce.

No tests exist for this skill today — its behavior is determined by
whether Claude Code actually follows the SKILL.md. The right integration
test is a dry run through all 6 phases on a reference design (e.g. UART
RX, future work).

## Relationship to the MCP skills

The generated skills in `.claude/skills/{spec2rtl,code_review,...}`
are **single-purpose LLM-backed tool wrappers** — they know about one
MCP tool each and trigger when the user's ask matches that tool's
intent.

`fpga_flow` is a **meta-skill** — it doesn't wrap an MCP tool; it
orchestrates calls to several tools across phases. If the user says
"generate an RTL from this spec" directly, the existing `spec2rtl`
skill fires as a one-shot, which is fine. If the user says "help me
build an FPGA project", `fpga_flow` fires and calls `spec2rtl` as part
of Phase 4.

Both skills can coexist in the same session; Claude Code picks the
more specific match for each user turn.
