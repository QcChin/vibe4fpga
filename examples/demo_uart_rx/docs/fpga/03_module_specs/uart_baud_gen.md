# Phase 3 — Module spec: `uart_baud_gen`

> Populated 2026-05-05 by the `fpga_flow` skill.
> **Status: approved.** Fed into Phase 4's `spec_to_rtl`.

## Source of truth
- `02_architecture.md` §Top-level module list — uart_baud_gen row
- `01_requirements.md` §FR-2 (115200 × 16 oversampling rate)

## Module identity
| Field         | Value |
|---------------|-------|
| Module name   | `uart_baud_gen` |
| File          | `rtl/uart_baud_gen.v` |
| Role          | Divide sys_clk down to a 1-cycle-wide enable pulse at 16× the baud rate |
| Clock domain  | sys_clk (50 MHz) |
| Reset         | active-low synchronous, shared with top-level `rst_n` |

## Port table

### Parameters
| Name          | Type   | Default | Allowed range | Meaning |
|---------------|--------|---------|---------------|---------|
| `CLK_HZ`      | `int`  | 50_000_000 | any positive int | system clock frequency in Hz |
| `BAUD_HZ`     | `int`  | 115200     | 9600..3_000_000 | target UART baud rate |
| `OVERSAMPLE`  | `int`  | 16         | 4, 8, 16       | oversampling factor; 16 is recommended |

The division constant is computed inside the module as
`CLK_HZ / (BAUD_HZ * OVERSAMPLE)`. With defaults, this is
50_000_000 / (115200 × 16) = 27 (integer truncation). Resulting
sampling rate = 50e6 / 16 / 27 = 115740.7 Hz, +0.47% from 115200, well
within NF-2's ±2% budget.

### Ports
| Direction | Name    | Width | Domain  | Protocol | Meaning |
|-----------|---------|-------|---------|----------|---------|
| input  | `clk`   | 1 | sys_clk | —           | rising-edge clock |
| input  | `rst_n` | 1 | sys_clk | —           | active-low synchronous reset |
| output | `tick`  | 1 | sys_clk | one-cycle enable pulse | asserted for 1 sys_clk every CLK_HZ/(BAUD_HZ×OVERSAMPLE) cycles |

### Protocol / handshake semantics
- **`tick` output**: rises on a posedge clk, stays high for exactly one
  cycle, then falls. Consumer (uart_rx FSM) samples tick on posedge
  clk. No ready/valid handshake — this is a one-sided free-running
  pulse.

## Behavior specification

### Reset behavior
When `rst_n == 0`:
- `tick` = 0
- internal divider counter = 0

### Normal operation
Starting from reset-deasserted (`rst_n = 1`):
- Cycle 0: internal counter = 0, `tick` = 0
- Cycle 1..(DIV-1): counter increments, `tick` = 0
- Cycle DIV: counter wraps to 0, `tick` = 1 for that one cycle
- Repeat

Where `DIV = CLK_HZ / (BAUD_HZ * OVERSAMPLE) - 1` (the "-1" because the
counter counts from 0 through DIV inclusive, which is DIV+1 total
cycles per period; adjust in implementation to match exact rate).

### Edge cases / error handling
- **CLK_HZ not divisible cleanly by (BAUD_HZ × OVERSAMPLE)**: integer
  division rounds down; introduces small baud error. Caller's
  responsibility to ensure the resulting error is within budget
  (NF-2).
- **Parameter out of range** (e.g. OVERSAMPLE=1): not enforced in
  hardware; caller contract says don't do that. `spec_to_rtl` should
  NOT emit runtime checks for this.
- **Reset asserted mid-count**: counter clears to 0 immediately (sync
  reset on next posedge clk), tick falls to 0 on same edge.

## Microarchitecture

### FSM
None. This is a pure counter + compare-to-zero module.

### Pipeline / dataflow
- **Combinational next-state logic**: `counter_next = (counter == DIV) ? 0 : counter + 1`
- **Registered state**: `counter` (holds across cycles), `tick`
  (registered output, not combinational, to avoid glitching)
- **Pipeline depth** (any input to `tick` output): 1 cycle

### Resource budget (estimate)
| Resource | Estimate | Basis |
|----------|----------|-------|
| LUTs     | ~10      | 9-bit counter (need ceil(log2(27))=5 bits, round up + compare logic) |
| FFs      | 6        | 5 counter bits + 1 registered tick |
| BRAMs    | 0        | |
| DSPs     | 0        | |

### Timing assumptions
- Max combinational delay: < 20 ns (50 MHz period). A 5-bit compare is
  trivial; plenty of margin.
- Multi-cycle paths: none
- False paths: none

## Verification hints (Phase 5)
- **Covpt-1**: tick period equals exactly `CLK_HZ/(BAUD_HZ*OVERSAMPLE)`
  sys_clk cycles between consecutive rising edges (check over a 10-byte
  span to average out rounding).
- **Covpt-2**: tick is high for exactly 1 cycle per period.
- **Covpt-3**: tick behavior identical across reset (i.e. apply reset
  mid-period, release, verify first tick comes at DIV cycles later,
  not earlier).
- **Covpt-4**: parameter sweep — verify correct tick period for
  (CLK_HZ, BAUD_HZ, OVERSAMPLE) ∈ {(50M, 9600, 16), (50M, 115200, 16),
  (12M, 115200, 16)} — sanity that the module is parameter-clean.

## Naming convention check
- Clock signal pattern: `clk` (project convention, confidence 1.0)
- Reset signal pattern: `rst_n` (project convention, confidence 0.75)

Both match. No renames needed.

---

## Phase 3 exit gate (this module)

- [x] Port table complete
- [x] Tick output protocol rule explicit
- [x] Reset behavior enumerated
- [x] Normal operation lifecycle described
- [x] Edge cases enumerated
- [x] FSM section correctly filled (N/A, with reason)
- [x] Resource budget estimated
- [x] Timing assumptions declared
- [x] ≥ 3 coverage points
- [x] Naming aligns with project convention
- [x] **User approval: approved uart_baud_gen spec (demo auto-approval)**
