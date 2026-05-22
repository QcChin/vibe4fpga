# End-to-end demo walkthrough

> 🌐 [中文](demo-walkthrough.zh.md) · **English**

A concrete 10-minute demo you can run today that exercises five of the eight
MCPs in sequence. Uses the tiny [`saturating_counter`](../examples/saturating_counter/)
DUT as a worked example.

**Prereqs:**
* MCPs installed (`make install`)
* Claude Code / Codex / OpenCode configured with the snippet from `configs/`
* `brew install icarus-verilog` (macOS) or oss-cad-suite (Windows)
* `ANTHROPIC_API_KEY` set in your environment

Every step below is a prompt you'd type into your agent host. The agent
routes each call to the right MCP tool — you don't invoke tools directly.

---

## 1 · Generate RTL from a spec

**Prompt:**
> Use `spec_to_rtl` to build me a 4-bit up/down saturating counter.
> It should clamp at `4'hF` when counting up and `4'h0` when counting
> down rather than wrapping. Expose `at_max` / `at_min` status flags.
> Active-low synchronous reset, 100 MHz target clock.

**What happens under the hood:**
* `fpga-project-mcp.spec_to_rtl` runs the 5-stage pipeline
  * stage 1 — parses your English into a `DesignIntent` JSON
  * stage 2 — flags ambiguities; a saturating counter is unambiguous, so no
    blocking questions are raised
  * stage 3 — scans your project dir (if one is open) for naming conventions
  * stage 4 — generates the RTL at temperature=0.1
  * stage 5 — a second LLM call audits the result; repairs up to 2 rounds

**What you get back:**
A `Spec2RTLResult` dict with fields like `rtl_code`, `self_check`, `score`,
and the audit trail of autonomous decisions. The RTL should look similar to
[`examples/saturating_counter/saturating_counter.v`](../examples/saturating_counter/saturating_counter.v).

---

## 2 · Review the generated RTL

**Prompt:**
> Now run `review_rtl` on what you just produced.

**What happens:**
* `fpga-project-mcp.review_rtl` ships the RTL through the 10-point FPGA
  pitfall checklist (latch inference, CDC, multi-driver, reset coverage,
  blocking/non-blocking, initial blocks, sensitivity lists, signed/unsigned,
  dangling ports)
* Returns `{findings: [...], error_count, warning_count, summary}`

For a clean counter you'd expect an empty findings list. If the spec2rtl
stage-5 self-check missed something subtle (e.g. reset on `at_max` being
combinational), code_review usually catches it.

---

## 3 · Generate a testbench

**Prompt:**
> Generate a self-checking testbench for the counter with
> `generate_testbench`. Cover reset recovery, saturation at both bounds,
> and the enable-held-low case. Target iverilog.

**What happens:**
* `verify-mcp.generate_testbench` builds a SystemVerilog testbench with
  clock generation, reset sequence, directed scenarios, SVA assertions,
  covergroup, and a watchdog
* Output has the `$display("PASS")` / `$display("FAIL")` convention so the
  simulator-log parser can score it

A reference testbench is [`examples/saturating_counter/tb_saturating_counter.sv`](../examples/saturating_counter/tb_saturating_counter.sv).

---

## 4 · Run the simulation

**Prompt:**
> Write both files to disk as `saturating_counter.v` and
> `tb_saturating_counter.sv`, then call `run_simulation` with
> simulator=icarus.

**What happens:**
* Agent host uses its built-in file-write tool to save both files
* `eda-bridge-mcp.run_simulation` compiles via `iverilog -g2012`, runs via
  `vvp`, captures stdout/stderr
* `parse_sim_log` interprets the output:
  * counts PASS / FAIL / `$error` / `$fatal` / assertion markers
  * surfaces up to 10 offending lines
  * collapses counts into a single `verdict` ∈ `{pass, fail, error, unclear}`

**You get back:**
```json
{
  "success":  true,
  "verdict":  "pass",
  "summary":  { "pass_count": 7, "fail_count": 0, ... },
  "stdout":   "...",
  "vcd_path": null
}
```

A `$finish` without any PASS markers would come back as `verdict=unclear`,
not a silent pass — this was the code-review-discovered gap that
`fix: critical post-review bugs` closed.

---

## 5 · If a test fails — debug the waveform

Suppose the testbench reported `verdict=fail`. Ask:

> Parse the VCD at `saturating_counter.vcd` with `debug_waveform` and tell
> me which signal misbehaved. Focus on the count and direction signals.

**What happens:**
* `waveform-mcp-rs.debug_waveform` loads the VCD via the Rust parser
* Runs the 5 deterministic detectors (glitches, X/Z, CDC, AXI handshake,
  stall timeout) concurrently on tokio
* The LLM reasoning layer (built-in Anthropic client) correlates detector
  findings with the spec — skipped automatically when no anomalies fire

---

## 6 · Score the full run

**Prompt:**
> Score this verification run with `score_verification`: use the sim log
> you captured above, plus the empty lint/formal/synth reports for now.
> Include the original spec as the compliance check.

**What happens:**
* `verify-mcp.score_verification` runs 5 stages:
  1. lint_report parse (deterministic regex)
  2. sim_log parse (reuses the same convention as eda-bridge-mcp)
  3. formal_report parse
  4. synth_report parse (WNS extraction)
  5. LLM spec-compliance check against the RTL
* Returns `{stages, score, verdict, overall_verdict, breakdown, notes, report_md}`

Verdict thresholds: **PASS ≥ 85**, **REVIEW 60-84**, **FAIL < 60**.

---

## What's *not* demonstrated here

* **Timing closure loop** — `suggest_timing_fix` reads Vivado reports and
  recommends fixes, but doesn't re-synthesise. User must apply the
  suggestion and re-run `run_synthesis` manually.
* **Full Xilinx flow** — requires Vivado. `eda-bridge-mcp.run_synthesis`
  wires up the TCL, but the PnR → bitstream steps are outside this demo.
* **iCE40 open-source flow** — `yosys-mcp` can take you from RTL to
  bitstream, but needs oss-cad-suite on PATH.
* **Oscilloscope correlation** — `instrument-mcp.analyze_instrument_diff`
  needs a real scope (or CSV capture) and `pyvisa` backend.

See [`docs/architecture.md`](architecture.md) for the full MCP inventory
and which one owns each capability.

---

## Rehearse offline

Without agent-host orchestration, you can still verify the machinery:

```bash
# 1. Run the reference testbench end-to-end.
cd examples/saturating_counter
iverilog -g2012 -o tb.vvp saturating_counter.v tb_saturating_counter.sv
vvp tb.vvp

# 2. Call run_simulation directly by piping stdio JSON-RPC.
#    (For real workflows let Claude Code / Codex / OpenCode do this.)
cd ../..
eda-bridge-mcp <<'REQ'
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"run_simulation",
           "arguments":{
             "project_path":"/abs/path/examples/saturating_counter",
             "testbench":"/abs/path/examples/saturating_counter/tb_saturating_counter.sv",
             "source_files":["/abs/path/examples/saturating_counter/saturating_counter.v"],
             "simulator":"icarus"}}}
REQ
```

(The trailing handshake + `initialize` call is normally handled by the agent
host; raw invocation like the above is mainly for debugging.)
