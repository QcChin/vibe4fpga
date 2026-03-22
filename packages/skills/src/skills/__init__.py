"""FPGA domain Skills — prompt engineering templates + EDA tool call pipelines.

Each Skill is a "prompt engineering template + tool call pipeline" that embeds
the thinking framework of an experienced FPGA engineer into the LLM.

Phase 1 (MVP):
  spec2rtl/     — Natural language spec → synthesizable RTL (5-stage pipeline)
  code_review/  — Common FPGA pitfall checker (latch, CDC, multi-driver, etc.)

Phase 2:
  timing_fix/        — Critical path analysis + 3 optimization strategies
  testbench_gen/     — Coverage-driven testbench generation (coverage ≥ 80%)
  waveform_debug/    — 5 parallel detectors + LLM reasoning (strict 4000-token budget)
  fsm_design/        — FSM from text description, state completeness check
  protocol_verify/   — AXI/PCIe/DDR/Ethernet compliance check

Phase 3:
  instrument_analyze/ — Sim vs. real-measurement root cause attribution

Phase 4:
  resource_optimize/ — DSP/BRAM usage optimization
"""
