# vibe4fpga Extended MCP Services Analysis

> 🌐 [中文](additional-mcp-services-analysis.zh.md) · **English**

> Document version: 2026-04-07
> Applies to: vibe4fpga (AI-assisted FPGA development IDE)
>
> **Note**: The `waveform-mcp` referenced below was absorbed into the Rust
> crate `waveform-mcp-rs` in v0.3.0. All 7 tools (parse / extract / stats /
> summarize / decode_axi / map_signal_to_rtl / debug_waveform) now live on
> the Rust side. The text below is historical design analysis — tool
> capabilities are unchanged, only the implementation language switched.

---

## Background & existing capability review

The MCP service layer in vibe4fpga today covers the core path from RTL
generation through hardware measurement:

| Existing MCP | Core capability |
|---|---|
| `fpga-project-mcp` | Project file indexing, module dependency graph, cross-file signal search, naming-convention analysis |
| `waveform-mcp` | VCD/FST parsing, 3-level compression, AXI4 decode, signal-to-RTL mapping |
| `datasheet-mcp` | LlamaIndex + Qdrant RAG; datasheet / protocol-spec / IP-interface retrieval |
| `eda-bridge-mcp` | Vivado synthesis / sim control, Lint, timing-report parsing |
| `instrument-mcp` | Oscilloscope CSV ingest, live SCPI capture, FFT, sim-vs-measurement alignment |
| `quartus-mcp` | QSF management, Quartus compile / timing analysis, USB-Blaster programming |
| `yosys-mcp` | Yosys iCE40/ECP5 synthesis, nextpnr P&R, SymbiYosys formal-prep |

These cover the mainstream development scenarios, but the actual FPGA
development lifecycle still has several critical gaps without MCP-layer
support. The analysis below proposes **10 high-value extension MCP
services**, each treated from three angles: tool list, technical
integration, and value proposition.

---

## Proposed services — detailed analysis

### 1. `constraint-mcp` — intelligent constraint-file management

#### Background pain

Timing constraints (XDC / SDC) are the single largest determinant of
FPGA implementation quality, and also the place where engineers most
often slip up. Wrong or missing constraints let synthesis pass while
on-board behaviour goes sideways. `eda-bridge-mcp` parses timing report
output today, but offers nothing for generating, validating, or
coverage-analysing the constraint files themselves.

#### Tool list

```
parse_xdc(xdc_file)
    → parse a Xilinx XDC file; return clock definitions, input/output
      delays, multi-cycle paths, false paths, and the port set each
      constraint touches

parse_sdc(sdc_file)
    → parse the Synopsys SDC format (Quartus TimeQuest, ECP5, ASIC flows)

generate_clock_constraints(rtl_files, top_module, target_freq_mhz)
    → identify clock ports from RTL signal analysis; emit
      create_clock / create_generated_clock drafts with recommended
      frequency and jitter margin

check_constraint_coverage(xdc_file, synthesis_netlist)
    → walk every timing path in the netlist; return the percent covered
      by constraints — {uncovered_paths, over_constrained_paths,
      coverage_pct}

suggest_cdc_constraints(rtl_files, waveform_file)
    → cross-reference RTL static analysis with dynamic waveform data
      to identify cross-clock-domain paths; recommend
      set_false_path or set_max_delay -datapath_only per path

validate_timing_exceptions(xdc_file, timing_report)
    → cross-check the multi-cycle and false-path declarations in the
      XDC against what the timing report actually shows; flag both
      "declared but no such path exists" and "undeclared but probably
      needs an exception" cases

diff_constraints(xdc_old, xdc_new)
    → diff two versions of a constraint file; highlight added /
      removed / modified constraints and their potential impact
```

#### Technical integration

- **Parser layer**: a hand-rolled lightweight XDC/SDC parser (regex +
  state machine, no EDA tool required at parse time), covering the
  Vivado XDC syntax subset.
- **Netlist analysis**: ingest Vivado-exported `.edf` or Yosys JSON
  netlists, build the timing-arc graph.
- **Coverage**: traverse the timing-arc graph with NetworkX; treat
  constraint entries as a set-cover problem.
- **CDC detection**: reuse `waveform-mcp`'s CDC results + RTL static
  port-tracking.

#### Value proposition

> Catch constraint mistakes at "RTL submit time" instead of "after the
> board is fabricated". Auto-generated constraints save a senior
> engineer 2-4 hours of repetitive busywork; coverage analysis catches
> the missing-constraint → false-positive-timing trap, sharply
> reducing first-board failure rates.

---

### 2. `power-mcp` — power analysis & optimisation

#### Background pain

Power is a hard constraint in FPGA design, especially in embedded /
low-power scenarios. Vivado Power Analyzer and Quartus PowerPlay
produce reports, but mapping those reports back to RTL design
decisions and producing actionable optimisation advice is purely
manual today.

#### Tool list

```
parse_power_report(report_file, tool)
    → parse Vivado (.xpr) or Quartus (.pow) power reports; return
      {static_mw, dynamic_mw, clock_mw, io_mw, logic_mw, bram_mw, dsp_mw}

estimate_switching_activity(vcd_file, netlist_file)
    → derive per-signal toggle rates from sim VCD; combine with netlist
      to estimate dynamic power per cell (a substitute for Vivado's
      .saif flow when XSim isn't available)

generate_power_constraints(activity_data, target_power_mw)
    → emit Vivado XDC set_switching_activity statements; pin precise
      toggle rates on key modules for higher-fidelity power sim

identify_power_hotspots(power_report, utilization_report)
    → locate hottest modules / clock domains; output Top-N high-power
      hierarchy paths cross-referenced to RTL source files

suggest_power_optimizations(rtl_files, power_report)
    → rule-base + LLM advice at the RTL level: clock gating, operand
      isolation, register-level power gating, swapping DSP for LUT
      multipliers, etc.

compare_power_scenarios(report_a, report_b, label_a, label_b)
    → compare two implementation versions' power distributions;
      quantify the impact of an optimisation
```

#### Technical integration

- **Report parsing**: dedicated parsers for Vivado XML power reports
  and the Quartus `.pow` format.
- **Toggle-rate statistics**: based on `waveform-mcp` waveform data,
  compute edge density per signal in Python.
- **Optimisation rule base**: 30+ FPGA power-optimisation patterns,
  with LLM doing the contextual reasoning.
- **Hotspot mapping**: module-name match maps power-report hierarchy
  paths to the `fpga-project-mcp` module tree.

#### Value proposition

> Move power optimisation from "look at the report after synthesis"
> to "AI proactively suggests at RTL time". For battery-powered or
> thermally constrained scenarios this can compress the iteration loop
> from days to hours and raise power-compliance rate by 30%+.

---

### 3. `version-control-mcp` — design evolution tracking

#### Background pain

FPGA design is file-centric, but tool-chain support for *semantic*
versioning of HDL code is essentially zero — Git can tell you a file
changed, but not "which ports changed in this commit" or "which
commit started the timing regression".

#### Tool list

```
get_rtl_diff(project_path, commit_a, commit_b)
    → extract HDL-semantic changes from Git diff: added / removed /
      modified ports, parameters, instances, signals
      (module-level semantic diff, not line-level)

track_timing_history(project_path, report_dir, n_commits)
    → analyse timing reports across the last N commits; plot
      WNS / TNS / Fmax curves; annotate the commit SHA that introduced
      each regression

blame_timing_violation(project_path, failing_path)
    → given a failing timing path, walk back to the earliest commit
      that introduced it; extract that commit's RTL change summary

create_design_snapshot(project_path, tag, metadata)
    → snapshot the current design state: timing score, resource
      utilisation, verification status; tie to a Git tag as a design
      checkpoint

compare_snapshots(snapshot_a, snapshot_b)
    → compare two design checkpoints across timing / resource / power /
      verification coverage; emit a Markdown comparison report

suggest_branch_strategy(project_path, goal)
    → recommend a Git branching strategy and milestone checkpoints
      given the current design phase and goal (e.g. "begin timing
      optimisation", "prepare for tape-out")
```

#### Technical integration

- **Git integration**: `gitpython` for history, diff, tag operations.
- **HDL semantic diff**: build on `fpga-project-mcp`'s module scanner;
  structured comparison of two scan results.
- **Historical report mapping**: agreed report-file naming convention
  indexed by commit hash.
- **Checkpoint storage**: JSON + SQLite light storage of per-snapshot
  quantitative metrics.

#### Value proposition

> Move FPGA design evolution from "I think I remember" to "data-driven
> traceback". Timing regression localisation drops from hours to
> minutes; design rollback has evidence; team code review gets an
> objective quality-delta basis.

---

### 4. `ip-catalog-mcp` — IP-core catalog & integration

#### Background pain

Modern FPGA design leans heavily on vendor IP (Vivado IP Catalog,
Intel IP Library) plus third-party open-source IP, but discovery,
versioning, interface wiring, and parameter configuration are mostly
done via the GUI — hard to automate. `datasheet-mcp` indexes IP
interface docs but can't drive actual configuration and
instantiation.

#### Tool list

```
list_vivado_ips(vivado_install_path, filter_category)
    → enumerate installed Vivado IPs filtered by category
      (FIFO / Memory / Math / Clock / ...); return
      {ip_name, vendor, version, categories, description}

get_ip_parameters(ip_vlnv, vivado_install_path)
    → return the full configurable parameter list (with defaults and
      valid ranges) for the given IP core (VLNV = Vendor:Library:Name:Version)

generate_ip_instantiation(ip_vlnv, parameters, module_name)
    → emit Verilog/SystemVerilog IP instantiation code from parameter
      config, with correct port mapping and parameter assignment

create_vivado_ip_tcl(ip_vlnv, parameters, output_dir)
    → emit Vivado create_ip + set_property TCL for batch IP
      configuration (no GUI required)

search_opencores_ip(description, protocol)
    → semantic search across a locally indexed OpenCores / FuseSoC
      library; return matching IP names, licences, interface types

check_ip_compatibility(ip_a, ip_b, connection_map)
    → validate interface compatibility between two IPs: data widths,
      clock domains, AXI version match

generate_ip_wrapper(ip_name, target_interface, project_conventions)
    → emit a project-style wrapper module around a third-party IP:
      signal renaming, width adaptation, clock-domain isolation
```

#### Technical integration

- **Vivado IP metadata**: parse the `.xml` IP description files under
  `<vivado_root>/data/ip/`.
- **FuseSoC integration**: shell out to the `fusesoc` CLI for
  open-source IP indexing.
- **Compatibility rules**: rule engine matching AXI4 / AXI4-Lite /
  AXI4-Stream standard interface widths and signal sets.
- **Code generation**: Jinja2 templates for instantiation code and TCL.

#### Value proposition

> IP integration goes from "read docs → click in GUI → hand-write
> instantiation" (hours) to "one LLM conversation". For system-level
> FPGA designs heavy on standard IP (MIG / PCIe / AXI Interconnect /
> SoC blocks), this cuts 30-50% off IP-integration time.

---

### 5. `formal-verify-mcp` — deep formal-verification integration

#### Background pain

`yosys-mcp` already ships a `prepare_formal_verification` tool
(outputs SMT2), and `verification/pipeline.py` Layer 3 has a
SymbiYosys stub — but the comment explicitly says "stub (full
SymbiYosys integration in Phase 4)". Formal verification has very high
value for protocol state machines, safety properties, and reset
behaviour; a dedicated MCP fills that gap.

#### Tool list

```
run_symbiyosys(sby_config_file, timeout_sec)
    → execute a SymbiYosys formal task; parse output;
      return {status: PASS/FAIL/UNKNOWN, property_results, counterexample_vcd}

generate_sby_config(rtl_files, top_module, mode, depth, engines)
    → emit a .sby config file from RTL files + verify params
      mode: prove | cover | bmc
      engines: smtbmc | abc | aiger

generate_sva_properties(rtl_code, spec, property_types)
    → use the LLM to emit SVA assertions from a natural-language spec
      property_types: [safety, liveness, protocol, reset, overflow]
      outputs an `ifdef FORMAL-guarded .sv property file

check_reset_behavior(rtl_files, top_module, reset_signal)
    → formal check on reset correctness: every register reaches a
      defined state on reset, no X propagation, no CDC violation
      in the reset domain

verify_fifo_properties(rtl_files, fifo_module)
    → FIFO-specialised formal: full / empty flag correctness,
      read/write pointer relations, no overflow/underflow, count
      monotonicity

extract_counterexample(sby_output_dir)
    → turn a SymbiYosys counterexample VCD into a human-readable
      state-sequence description that an LLM can root-cause

run_equivalence_check(golden_rtl, revised_rtl, top_module)
    → Yosys `equiv` for combinational / sequential equivalence
      between two RTL versions; detect whether a refactor / optimisation
      accidentally changed behaviour
```

#### Technical integration

- **SymbiYosys**: directly invoke the `sby` CLI; parse logs and VCDs
  under `output/`.
- **SVA generation**: LLM (Claude / RTLCoder) + few-shot prompts;
  output IEEE 1800-2017-compliant SVA.
- **Equivalence check**: invoke `yosys -p "equiv_check"`; parse the
  result.
- **Counterexample analysis**: reuse `waveform-mcp` VCD parsing to
  structure the counterexample waveform.

#### Value proposition

> Formal finds hidden bugs in regions of state space that simulation
> can't reach — especially relevant for FIFOs, FSMs, arbiters, and
> other protocol logic. Compared to sim, formal provides
> mathematical-level guarantees on safety-critical properties. This
> service drops the formal-verification entry barrier from "expert
> writes SVA and configures the tool chain" to "describe the property
> in natural language; AI handles the rest".

---

### 6. `board-bringup-mcp` — board-level debug & bring-up

#### Background pain

The hardest phase of FPGA development is usually "board bring-up":
the bitstream downloads fine, but nothing on the board works.
This phase needs frequent JTAG operations, register reads/writes,
ILA data analysis, and cross-referencing the schematic — none of
which has any vibe4fpga MCP coverage today.

#### Tool list

```
scan_jtag_chain(interface, speed_khz)
    → scan the JTAG chain via OpenOCD / Vivado hw_server; return
      discovered devices {idcode, ir_length, device_name}

read_register(interface, address, width_bits)
    → read a target register via JTAG or UART
      supports: AXI4-Lite address space, custom DR scan chains

write_register(interface, address, value, mask)
    → write a target register with read-modify-write mask support

capture_ila_data(vivado_project, ila_core_name, trigger_config, max_depth)
    → configure and arm a Xilinx ILA (Integrated Logic Analyzer);
      wait for trigger; extract the VCD for waveform-mcp to analyse

parse_ila_ltx(ltx_file)
    → parse Vivado ILA probe files (.ltx); return per-probe
      signal name, width, clock domain

configure_signaltap(quartus_project, stp_file, trigger)
    → configure Intel SignalTap II; download capture config to the
      FPGA; wait for trigger; extract sampled data

analyze_uart_log(log_file_or_port, baud_rate, protocol)
    → parse board-level UART debug output; recognise known error
      patterns. supports: plain text, simple frame protocols, SLIP

correlate_ila_with_simulation(ila_vcd, sim_vcd, signal_map)
    → align ILA capture with sim waveform; identify "passes sim,
      fails hardware" signal deltas; invoke instrument-mcp's
      classification engine for root-cause categorisation
```

#### Technical integration

- **JTAG access**: OpenOCD (open) or Vivado `hw_server` TCL API (Xilinx).
- **ILA control**: Vivado TCL API (`open_hw_manager` / `run_hw_ila`)
  driven in batch.
- **SignalTap**: the `quartus_stp` command-line tool.
- **UART parsing**: `pyserial` + protocol state machine.
- **Waveform correlation**: reuse `instrument-mcp`'s
  `align_with_simulation` + `classify_differences_tool`.

#### Value proposition

> Board bring-up is the most "intuition-driven" phase of FPGA
> development, and the hardest to automate. This service uses
> AI-assisted JTAG ops, intelligent ILA analysis, and sim-vs-hardware
> diff to turn bring-up from "black-box poking" into "data-driven
> systematic triage" — cutting average bring-up time by 40-60%.

---

### 7. `perf-profiling-mcp` — performance & resource profiling

#### Background pain

`eda-bridge-mcp` already parses Vivado timing reports (WNS / TNS),
but deep utilisation analysis, critical-path RTL traceback, LUT-level
optimisation advice, and BRAM/DSP inference strategy are unsupported.
Engineers still hand-parse large tables when reading synthesis
reports.

#### Tool list

```
parse_utilization_detail(report_file, tool)
    → deep parse of utilisation reports: not just totals but per-hierarchy
      breakdown, carry-chain usage, SRL usage, clock-buffer resources

analyze_critical_path(timing_report, rtl_root)
    → extract the critical path from the timing report; trace back to
      RTL line numbers; annotate logic-level count and per-segment
      delay contribution

identify_resource_inefficiencies(utilization_report, rtl_files)
    → recognise inefficient resource-use patterns:
      - LUT6s used as small MUXes (should use casex / priority encoding)
      - BRAM inferred as LUTRAM (needs attribute or rewrite)
      - DSP not inferred (multiply-add tree problem)
      - FF utilisation far below LUT (wide combinational bottleneck)

suggest_pipeline_insertion(timing_report, rtl_files, target_freq_mhz)
    → suggest pipeline registers on critical paths: point at the
      assign statement to slice; estimate the resulting timing
      improvement

compare_resource_usage(report_a, report_b)
    → compare two versions' utilisation; quantify per-resource delta

estimate_floorplan_congestion(utilization_report, part)
    → estimate routing congestion risk from resource utilisation;
      recommend module split / reorg when an area exceeds 80%
```

#### Technical integration

- **Report parsing**: dedicated parsers for Vivado HTML/XML utilisation
  reports and Quartus Fitter reports.
- **Critical-path traceback**: parse the `datapath` lines from the
  timing report; reverse-map netlist cell names to
  `fpga-project-mcp` module signals.
- **Efficiency rule base**: 20+ Xilinx/Intel resource-inference best
  practices encoded.
- **Pipeline suggestions**: LLM code understanding + timing-delay data
  → directly insertable RTL patches.

#### Value proposition

> Free the engineer from "stare at 300 lines of synthesis report and
> guess the problem". Critical-path RTL traceback turns timing closure
> from "search the whole design" into "precise surgery"; combined with
> AgentLoop it enables fully automated timing-closure iteration.

---

### 8. `cocotb-mcp` — Python verification framework integration

#### Background pain

`eda-bridge-mcp` supports Icarus Verilog / Verilator / xsim and the
`testbench_gen` skill emits SystemVerilog testbenches, but there's
no cocotb support. cocotb is the modern FPGA verification framework
of choice (Python coroutines, randomisation, coverage, pytest
integration). Its absence is a major gap.

#### Tool list

```
generate_cocotb_test(rtl_module, spec, protocol, coverage_goals)
    → LLM-generated cocotb Python test file:
      @cocotb.test() functions, clock driver, randomised stimulus,
      assertion checks, coverage-bin definitions

run_cocotb_test(test_file, dut_files, simulator, timeout_sec)
    → run a cocotb test suite; capture pytest output;
      return {passed, failed, errors, coverage_xml, vcd_path}

parse_coverage_report(coverage_xml)
    → parse cocotb-coverage or Verilator coverage XML;
      return functional / line / branch coverage summary

identify_coverage_holes(coverage_report, spec)
    → find functional scenarios not covered by tests;
      recommend supplementary test cases

generate_uvm_like_sequence(protocol, transaction_types, randomization_weights)
    → emit cocotb-style UVM-like stimulus sequence classes;
      protocol-aware random transaction gen (AXI4 / UART / SPI / I2C)

run_regression(test_dir, parallel_jobs, seed_list)
    → parallel regression run; aggregate multi-seed results;
      pass-rate / failing-seed list / coverage summary
```

#### Technical integration

- **cocotb integration**: drive cocotb via `Makefile` or call
  `pytest --co` for discovery and execution.
- **Coverage**: cocotb-coverage library + Verilator `--coverage`.
- **Parallelism**: `asyncio.gather` + process pool, one process per
  seed.
- **Code generation**: LLM (Claude) + cocotb-dedicated prompt
  templates covering the mainstream protocol test patterns.

#### Value proposition

> cocotb has become the FPGA verifier's tool of choice, particularly
> when complex stimulus modelling is needed (random packet generation,
> error injection). This service upgrades vibe4fpga from "generates
> SV testbenches" to "supports the Python verification ecosystem",
> and via coverage-driven auto-completion of tests closes the
> verification loop.

---

### 9. `git-review-mcp` — HDL code review & quality gate

#### Background pain

`eda-bridge-mcp` has `run_lint`; the `code_review` skill has an
LLM-driven design review — both file-level full-pass. In a Git
workflow what engineers actually want is "what changed in this
commit, and did the change introduce issues" — incremental analysis,
plus automated quality gates that drop into the CI/CD pipeline.

#### Tool list

```
review_staged_changes(project_path, base_commit)
    → analyse working-tree HDL diffs against a base commit;
      run Lint + LLM review on changed lines only;
      emit an incremental issue list (no repeats of pre-existing issues)

check_design_rules(rtl_files, rule_profile)
    → configurable design-rule checks:
      rule_profile: "conservative" | "aggressive" | custom JSON
      rules cover: CDC risk, reset strategy, FSM encoding, latch
      inference, timing-path risk, naming-convention violations

generate_review_comment(file, line_start, line_end, issue_type)
    → emit a human-readable review comment for a specific code
      location: issue description, severity, fix recommendation,
      reference example

create_ci_quality_gate(project_path, thresholds)
    → emit a CI quality-gate config (GitHub Actions / GitLab CI YAML):
      thresholds: {max_errors: 0, max_warnings: 10, min_timing_margin_ns: 0.5}

calculate_quality_metrics(project_path)
    → overall project code-quality metrics:
      {lint_score, cdc_risk_score, naming_consistency,
       comment_coverage, module_complexity_avg}

enforce_naming_conventions(project_path, convention_file)
    → check naming-convention violations across the project against
      an agreed convention file; emit an auto-fixable rename list
```

#### Technical integration

- **Incremental analysis**: combine `gitpython` diff with
  `fpga-project-mcp`'s file scan; only run analysis on changed
  modules.
- **Rule engine**: extensible rule config (YAML); built-in rule base
  covers Xilinx / Intel best practices.
- **CI integration**: emit GitHub Actions workflow YAML that can
  auto-post review comments on PRs.
- **Quality metrics**: based on the `code_review` skill's scoring +
  static metrics (cyclomatic-complexity approximation).

#### Value proposition

> Bake AI code review into the Git workflow — every commit/PR gets
> professional-grade FPGA design review automatically. Junior
> engineers get instant guidance; senior engineers get reclaimed
> hours; quality gates keep low-quality code off main.

---

### 10. `protocol-checker-mcp` — on-chip protocol compliance

#### Background pain

`waveform-mcp` has an AXI4 decoder, `instrument-mcp` has diff
classification, but systematic, comprehensive protocol-compliance
checking is missing — e.g. AXI4's 200+ protocol rules, UART / SPI /
I2C timing-parameter compliance, PCIe link-layer behaviour
verification. Protocol bugs are the most common IP-integration
failure mode and deserve dedicated support.

#### Tool list

```
check_axi4_compliance(waveform_file, axi_prefix, clock_name)
    → comprehensive AXI4 protocol compliance check covering the ARM
      AMBA spec's key rules:
      - VALID cannot wait for READY before asserting
      - signal values cannot change after handshake completes
      - ARLEN / AWLEN match actual transfer beats
      - WSTRB matches WDATA width
      - 5-channel independence (AW / W / B / AR / R don't interleave
        illegally)
      returns: {violations: [{rule_id, description, timestamp_ns, evidence}]}

check_uart_timing(waveform_file, signal_name, baud_rate, tolerance_pct)
    → validate UART signal timing compliance: start-bit width,
      data-bit spacing, stop-bit length — within tolerance counts as
      compliant

check_spi_protocol(waveform_file, sclk, mosi, miso, cs, mode)
    → SPI protocol compliance:
      mode 0/1/2/3 phase/polarity match, CS setup/hold, MOSI/MISO
      stable region

check_i2c_protocol(waveform_file, scl, sda, speed_mode)
    → I2C protocol compliance:
      START/STOP conditions, ACK/NACK correctness, clock stretching,
      bus arbitration (speed_mode: standard / fast / fast-plus)

detect_protocol_from_waveform(waveform_file, signals)
    → auto-detect what protocol a signal group implements;
      return ranked candidates with confidence

generate_protocol_assertions(protocol, interface_prefix)
    → emit a complete SVA assertion file for the given protocol;
      directly usable in simulation assertion or formal verification
```

#### Technical integration

- **AXI4 check**: extend `waveform-mcp`'s existing AXI4 decoder with
  the full ARM AMBA rule set (ref. IHI0022H).
- **Serial protocol analysis**: state-machine-based protocol decode
  coupled with `waveform-mcp`'s signal-extraction API.
- **Protocol detection**: signal-name pattern matching + waveform
  statistical features → protocol classifier.
- **SVA generation**: reference open-source protocol SVA libraries
  (AXI4-SVA, AMBA-SVIP) to emit structured assertions.

#### Value proposition

> Protocol violations are the hardest class of bugs to track down —
> often invisible in RTL sim, only surfacing under real-hardware
> stress. This service upgrades protocol-compliance checks from
> "depends on a scope and human eyeballs" to "automated rule engine
> + AI explanation". Catching violations at sim time dramatically
> de-risks board-level integration.

---

## Service priority matrix

A combined assessment across "development frequency", "current pain
intensity", and "implementation difficulty":

| Service | Dev value | Impl complexity | Recommended priority |
|---|---|---|---|
| `constraint-mcp` | very high | medium | P0 — start now |
| `formal-verify-mcp` | high | medium (infra already in place) | P0 — start now |
| `board-bringup-mcp` | very high | high | P1 — next iteration |
| `perf-profiling-mcp` | high | medium | P1 — next iteration |
| `ip-catalog-mcp` | high | medium | P1 — next iteration |
| `protocol-checker-mcp` | high | medium (AXI base already there) | P1 — next iteration |
| `cocotb-mcp` | mid-high | medium | P2 — later |
| `power-mcp` | mid-high | medium | P2 — later |
| `version-control-mcp` | medium | low | P2 — later |
| `git-review-mcp` | medium | low (rides existing capabilities) | P2 — later |

---

## Inter-service dependencies

```
fpga-project-mcp ──┬──> constraint-mcp     (module signals → clock inference)
                   ├──> perf-profiling-mcp (critical-path RTL traceback)
                   ├──> git-review-mcp     (incremental analysis baseline)
                   └──> ip-catalog-mcp     (interface compatibility checks)

waveform-mcp ──────┬──> formal-verify-mcp     (counterexample VCD analysis)
                   ├──> protocol-checker-mcp  (waveform signal extraction)
                   └──> board-bringup-mcp     (ILA data analysis)

eda-bridge-mcp ────┬──> constraint-mcp     (timing report validates constraints)
                   ├──> perf-profiling-mcp (synthesis / P&R reports)
                   └──> power-mcp          (power-report input)

instrument-mcp ────> board-bringup-mcp     (sim-vs-measurement reuse)

yosys-mcp ─────────> formal-verify-mcp     (SMT2 preparation)

datasheet-mcp ─────> ip-catalog-mcp        (IP doc RAG backing)
```

---

## Extended full MCP architecture

```
VSCode Extension
       │  SSE / HTTP
       ▼
  LLM Router (:8765)
       │
  ┌────┴───────────────────────────────────────────────────────────┐
  │                        Skill Engine                            │
  │  Spec2RTL · CodeReview · WaveformDebug · TimingFix             │
  │  TestbenchGen · Verification · InstrumentAnalyze · AgentLoop   │
  └────┬───────────────────────────────────────────────────────────┘
       │  MCP Protocol
  ┌────┴──────────────────────────────────────────────────────────────┐
  │                        MCP Servers                                │
  │                                                                   │
  │  ── Existing ───────────────────────────────────────────────────  │
  │  fpga-project · eda-bridge · waveform · instrument · datasheet    │
  │  quartus · yosys                                                  │
  │                                                                   │
  │  ── Proposed (this document) ─────────────────────────────────── │
  │  constraint · power · version-control · ip-catalog                │
  │  formal-verify · board-bringup · perf-profiling                   │
  │  cocotb · git-review · protocol-checker                           │
  └────┬──────────────────────────────────────────────────────────────┘
       │
  EDA Tools / Hardware / VCS
  Vivado · Quartus · Yosys · nextpnr · SymbiYosys · cocotb
  Oscilloscope · Logic Analyzer · JTAG · ILA · SignalTap
  Git · GitHub Actions · OpenCores / FuseSoC
```

---

## Implementation roadmap

### Phase 5 (near-term): close the constraint & formal-verify gaps

1. Implement `constraint-mcp`, prioritising XDC parsing and auto-clock-
   constraint generation
2. Spin `formal-verify-mcp` out of `yosys-mcp` as a standalone service;
   implement the full SymbiYosys flow
3. Wire `verification/pipeline.py` Layer 3 to the real
   `formal-verify-mcp`

### Phase 6 (medium-term): performance & bring-up

1. Implement `perf-profiling-mcp`, prioritising Vivado critical-path
   RTL traceback
2. Implement `board-bringup-mcp`, prioritising ILA capture +
   `waveform-mcp` interop
3. Implement `ip-catalog-mcp`, prioritising Vivado IP catalog parsing
   and instantiation generation

### Phase 7 (long-term): process automation & quality system

1. Implement `cocotb-mcp` — Python verification ecosystem
2. Implement `protocol-checker-mcp` — extend the AXI4 checker to the
   full rule set, add the serial protocols
3. Implement `power-mcp` and `version-control-mcp`
4. Implement `git-review-mcp` — wire up CI/CD integration

---

*This document was generated by Claude Code from analysis of the
vibe4fpga repository source, 2026-04-07.*
