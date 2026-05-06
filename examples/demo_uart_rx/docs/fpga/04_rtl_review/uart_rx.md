# Phase 4 — `uart_rx` review

> Auto-generated 2026-05-06. Corresponds to `rtl/uart_rx.v` and
> `03_module_specs/uart_rx.md`. This demo skipped the full spec2rtl
> 5-stage pipeline; it used the Stage-4 RTL_GENERATOR prompt directly.

## Artifact
- RTL file: `rtl/uart_rx.v` (8576 characters)

## review_rtl result
| Severity | Count |
|----------|-------|
| ERROR    | 1 |
| WARNING  | 5 |

### Errors
- **ERROR** [style] line 17: MID_START = (OVERSAMPLE/2)-1. For OVERSAMPLE=16 this gives 7, meaning the start bit is sampled after 7 ticks (0..6 counted, sample on 7th tick). However tick_cnt starts at 0 and increments on each tick, so the sample occurs at tick_cnt==7 which is the 8th tick — correct for mid-bit of a 16x oversample. But if OVERSAMPLE is set to a value <=2, (OVERSAMPLE/2)-1 underflows to a large unsigned value, causing incorrect behavior.

### Warnings
- **WARNING** [style] line 85: Explicit self-assignment 'shift_reg <= shift_reg' in S_IDLE is redundant and may confuse synthesis tools, though functionally harmless.
- **WARNING** [reset] line 46: Resetting the 2-FF synchronizer registers (rx_in_meta, rx_in_sync) to a fixed value (1'b1) during reset is generally unnecessary and can cause a false start-bit detection glitch on reset release if rx_in is already low. The reset of synchronizer FFs is typically omitted or handled carefully.
- **WARNING** [style] line 270: rx_ready input is declared but not functionally used. The design does not implement backpressure: rx_valid is asserted for one cycle regardless of rx_ready, and a new byte can overwrite rx_data before the consumer reads it.
- **WARNING** [style] line 60: rx_valid and rx_frame_error are cleared to 0 every cycle by default assignments before the case statement. If the FSM is in S_STOP and tick is not asserted, these signals are deasserted. This is correct pulse behavior, but the rx_valid pulse in S_STOP only lasts one clock cycle. If the downstream consumer misses it (e.g., due to rx_ready not being checked), data is silently lost.
- **WARNING** [reset] line 63: rx_data is reset to 0 and only updated on a valid stop bit. This is correct, but rx_data retains the last successfully received byte indefinitely. If rx_valid is missed by the consumer, stale data remains visible. This is a protocol concern rather than a synthesis bug, but worth noting.

### review_rtl summary
1 error(s), 5 warning(s) found in uart_rx.v

### Raw findings JSON
```json
[
  {
    "severity": "warning",
    "category": "style",
    "line": 85,
    "signal": "shift_reg",
    "message": "Explicit self-assignment 'shift_reg <= shift_reg' in S_IDLE is redundant and may confuse synthesis tools, though functionally harmless.",
    "fix": "Remove the redundant self-assignment; registers hold their value by default in sequential logic."
  },
  {
    "severity": "warning",
    "category": "reset",
    "line": 46,
    "signal": "rx_in_meta",
    "message": "Resetting the 2-FF synchronizer registers (rx_in_meta, rx_in_sync) to a fixed value (1'b1) during reset is generally unnecessary and can cause a false start-bit detection glitch on reset release if rx_in is already low. The reset of synchronizer FFs is typically omitted or handled carefully.",
    "fix": "Consider removing reset from the synchronizer FFs, or ensure the FSM stays in S_IDLE long enough after reset de-assertion to avoid false triggering."
  },
  {
    "severity": "error",
    "category": "style",
    "line": 17,
    "signal": "MID_START",
    "message": "MID_START = (OVERSAMPLE/2)-1. For OVERSAMPLE=16 this gives 7, meaning the start bit is sampled after 7 ticks (0..6 counted, sample on 7th tick). However tick_cnt starts at 0 and increments on each tick, so the sample occurs at tick_cnt==7 which is the 8th tick — correct for mid-bit of a 16x oversample. But if OVERSAMPLE is set to a value <=2, (OVERSAMPLE/2)-1 underflows to a large unsigned value, causing incorrect behavior.",
    "fix": "Add a parameter constraint check: ensure OVERSAMPLE >= 4. Use an assertion or generate-time check: if (OVERSAMPLE < 4) $error(...)."
  },
  {
    "severity": "warning",
    "category": "style",
    "line": 270,
    "signal": "unused_rx_ready",
    "message": "rx_ready input is declared but not functionally used. The design does not implement backpressure: rx_valid is asserted for one cycle regardless of rx_ready, and a new byte can overwrite rx_data before the consumer reads it.",
    "fix": "Implement proper handshaking: hold rx_valid high and stall the FSM (remain in S_IDLE or a holding state) until rx_ready is asserted, preventing data loss."
  },
  {
    "severity": "warning",
    "category": "style",
    "line": 60,
    "signal": "rx_valid",
    "message": "rx_valid and rx_frame_error are cleared to 0 every cycle by default assignments before the case statement. If the FSM is in S_STOP and tick is not asserted, these signals are deasserted. This is correct pulse behavior, but the rx_valid pulse in S_STOP only lasts one clock cycle. If the downstream consumer misses it (e.g., due to rx_ready not being checked), data is silently lost.",
    "fix": "Implement a valid/ready handshake: keep rx_valid asserted until rx_ready is seen, and gate FSM progression on the handshake completing."
  },
  {
    "severity": "info",
    "category": "style",
    "line": 95,
    "signal": "state",
    "message": "The FSM uses 11 states (S_IDLE through S_STOP) encoded in 4 bits with sequential encoding. This is fine, but the data states S_D0–S_D7 are structurally identical and could be collapsed into a single state with a 3-bit bit-index counter, significantly reducing code size and improving maintainability.",
    "fix": "Refactor data states into a single S_DATA state with a bit_cnt[2:0] counter, transitioning to S_STOP when bit_cnt reaches 7."
  },
  {
    "severity": "info",
    "category": "cdc",
    "line": 44,
    "signal": "rx_in_meta",
    "message": "The 2-FF synchronizer is correctly implemented. However, there is no ASYNC_REG or equivalent synthesis attribute applied to rx_in_meta and rx_in_sync to prevent the synthesizer from merging or optimizing these registers, which could break MTBF guarantees on some FPGA families.",
    "fix": "Add (* ASYNC_REG = \"TRUE\" *) attribute (Xilinx) or equivalent (Intel: (* altera_attribute = \"-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS\" *)) to rx_in_meta and rx_in_sync."
  },
  {
    "severity": "warning",
    "category": "reset",
    "line": 63,
    "signal": "rx_data",
    "message": "rx_data is reset to 0 and only updated on a valid stop bit. This is correct, but rx_data retains the last successfully received byte indefinitely. If rx_valid is missed by the consumer, stale data remains visible. This is a protocol concern rather than a synthesis bug, but worth noting.",
    "fix": "Document that rx_data is only valid when rx_valid is high, or implement a handshake mechanism."
  }
]
```

---

## Phase 4 exit gate (this module)

- [ ] review_rtl reports zero ERROR severity findings
- [x] Manual edits: none yet (auto-generated)
- [ ] User approval: demo auto-approved (no real user sign-off)
