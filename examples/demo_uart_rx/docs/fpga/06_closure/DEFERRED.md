# Phase 6 — DEFERRED

Phase 6 (synthesis / timing / bring-up) is not executed in this demo.

## Why

Executing Phase 6 needs at least one of:
- Yosys + nextpnr-ice40 + icepack (for the open-source iCE40 flow
  declared in Phase 1 §Target platform) — not installed on this machine
- Vivado — not installed
- Quartus — not installed

## What unblocks Phase 6

Easiest path is the Lattice iCE40 open-source flow (what Phase 1 picked):

```bash
# Install oss-cad-suite from https://github.com/YosysHQ/oss-cad-suite-build/releases
# Then this demo can run:

mcp__yosys-mcp__synthesize_ice40    # → netlist JSON
mcp__yosys-mcp__pnr_ice40           # → .asc
mcp__yosys-mcp__pack_ice40_bitstream # → .bin
```

Or switch to Yosys generic synthesis (gives resource-count answers
without needing nextpnr):

```bash
mcp__yosys-mcp__synthesize_generic   # → cell count + JSON netlist only
```

Phase 6 is optional for the demo's purpose (proving the `fpga_flow`
skill drives the stages 1-5 correctly), so it is parked here as a
documented gap rather than a flow failure.
