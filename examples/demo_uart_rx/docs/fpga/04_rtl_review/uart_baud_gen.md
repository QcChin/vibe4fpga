# Phase 4 — `uart_baud_gen` review

> Auto-generated 2026-05-06. Corresponds to `rtl/uart_baud_gen.v` and
> `03_module_specs/uart_baud_gen.md`. This demo skipped the full spec2rtl
> 5-stage pipeline; it used the Stage-4 RTL_GENERATOR prompt directly.

## Artifact
- RTL file: `rtl/uart_baud_gen.v` (1907 characters)

## review_rtl result
| Severity | Count |
|----------|-------|
| ERROR    | 1 |
| WARNING  | 3 |

### Errors
- **ERROR** [style] line 12: When DIV == 1, CNT_W is set to 1, but the counter comparison 'counter == CNT_W'(DIV - 1)' becomes 'counter == 0', meaning tick fires every cycle. The counter increment path still executes before wrapping, which is functionally correct but the CNT_W=1 case with DIV=1 means the else branch (increment) is never taken — this is correct but the increment expression '{{(CNT_W-1){1'b0}}, 1'b1}' with CNT_W=1 produces a zero-width replication '{{0{1'b0}}, 1'b1}' which is a zero-width concatenation and is illegal in many tools.

### Warnings
- **WARNING** [reset] line 28: tick is declared as 'output reg' but driven inside an always_ff block. Mixed port declaration style (reg vs logic) with SystemVerilog always_ff may cause tool inconsistencies.
- **WARNING** [style] line 11: DIV is computed using integer division which silently truncates. If CLK_HZ is not evenly divisible by (BAUD_HZ * OVERSAMPLE), the baud rate will be inaccurate with no warning to the user.
- **WARNING** [style] line 36: The increment 'counter + {{(CNT_W-1){1'b0}}, 1'b1}' uses a replication of (CNT_W-1) zeros. When CNT_W=1, this becomes {{0{1'b0}}, 1'b1} which is a zero-width replication — illegal per IEEE 1800 and will cause elaboration errors in strict-mode tools.

### review_rtl summary
1 error(s), 3 warning(s) found in uart_baud_gen.v

### Raw findings JSON
```json
[
  {
    "severity": "warning",
    "category": "reset",
    "line": 28,
    "signal": "tick",
    "message": "tick is declared as 'output reg' but driven inside an always_ff block. Mixed port declaration style (reg vs logic) with SystemVerilog always_ff may cause tool inconsistencies.",
    "fix": "Declare tick as 'output logic tick' to be consistent with SystemVerilog usage alongside always_ff."
  },
  {
    "severity": "warning",
    "category": "style",
    "line": 11,
    "signal": null,
    "message": "DIV is computed using integer division which silently truncates. If CLK_HZ is not evenly divisible by (BAUD_HZ * OVERSAMPLE), the baud rate will be inaccurate with no warning to the user.",
    "fix": "Add a localparam or generate check to verify CLK_HZ % (BAUD_HZ * OVERSAMPLE) == 0, or document the acceptable error margin."
  },
  {
    "severity": "error",
    "category": "style",
    "line": 12,
    "signal": "counter",
    "message": "When DIV == 1, CNT_W is set to 1, but the counter comparison 'counter == CNT_W'(DIV - 1)' becomes 'counter == 0', meaning tick fires every cycle. The counter increment path still executes before wrapping, which is functionally correct but the CNT_W=1 case with DIV=1 means the else branch (increment) is never taken — this is correct but the increment expression '{{(CNT_W-1){1'b0}}, 1'b1}' with CNT_W=1 produces a zero-width replication '{{0{1'b0}}, 1'b1}' which is a zero-width concatenation and is illegal in many tools.",
    "fix": "Guard the increment expression or use 'counter + 1'b1' instead of the explicit zero-extension concatenation to avoid zero-width replication when CNT_W=1."
  },
  {
    "severity": "warning",
    "category": "style",
    "line": 36,
    "signal": "counter",
    "message": "The increment 'counter + {{(CNT_W-1){1'b0}}, 1'b1}' uses a replication of (CNT_W-1) zeros. When CNT_W=1, this becomes {{0{1'b0}}, 1'b1} which is a zero-width replication — illegal per IEEE 1800 and will cause elaboration errors in strict-mode tools.",
    "fix": "Replace with 'counter + 1'b1' or 'counter + {{CNT_W}{1'b0}} + 1'b1' cast properly, or simply 'counter + 1' with appropriate width."
  },
  {
    "severity": "info",
    "category": "style",
    "line": 17,
    "signal": null,
    "message": "$error() inside a generate block is a SystemVerilog elaboration system task. While widely supported, it is not universally supported by all synthesis tools and may be silently ignored rather than causing a hard error.",
    "fix": "Supplement with an illegal instantiation trick (e.g., instantiating a non-existent module) to guarantee a hard elaboration failure across all tools, or rely on simulation/lint to catch this."
  },
  {
    "severity": "info",
    "category": "style",
    "line": 44,
    "signal": null,
    "message": "Formal assertions using 'assert property' are placed outside any procedural block at module scope. While legal in SystemVerilog, some tools require them inside a module scope checker or expect them wrapped in an 'always' or checker construct for portability.",
    "fix": "Wrap formal assertions in a dedicated checker or use 'always_comb'/'always_ff' scoped assertions, or ensure the formal tool supports module-level concurrent assertions."
  }
]
```

---

## Phase 4 exit gate (this module)

- [ ] review_rtl reports zero ERROR severity findings
- [x] Manual edits: none yet (auto-generated)
- [ ] User approval: demo auto-approved (no real user sign-off)
