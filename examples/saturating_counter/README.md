# saturating_counter — reference DUT

A tiny 4-bit up/down counter that clamps at its bounds instead of wrapping.
Used as the worked example throughout
[`docs/demo-walkthrough.md`](../../docs/demo-walkthrough.md).

## Files

| File | Role |
| ---- | ---- |
| `saturating_counter.v` | Synthesisable RTL — matches spec2rtl's style conventions |
| `tb_saturating_counter.sv` | Self-checking testbench, emits PASS/FAIL markers `parse_sim_log` understands |

## Run by hand

```bash
iverilog -g2012 -o tb.vvp \
    saturating_counter.v tb_saturating_counter.sv
vvp tb.vvp
```

Expect `All tests passed (6/6)` and `$finish` at ~1000 ns.

## Run through the MCPs

Equivalent via `eda-bridge-mcp.run_simulation`:

```json
{
  "project_path": "/absolute/path/to/examples/saturating_counter",
  "testbench":    "/absolute/path/to/examples/saturating_counter/tb_saturating_counter.sv",
  "source_files": ["/absolute/path/to/examples/saturating_counter/saturating_counter.v"],
  "simulator":    "icarus"
}
```

Returned summary:
```json
{
  "success":  true,
  "verdict":  "pass",
  "summary":  {
    "pass_count":        7,
    "fail_count":        0,
    "fatal_count":       0,
    "error_count":       0,
    "assertions_passed": 0,
    "assertions_failed": 0
  }
}
```
