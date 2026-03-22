"""AXI4 5-channel state machine decoder.

Channels: AW (write address), W (write data), B (write response),
          AR (read address), R (read data).

Handshake rule: transfer occurs when both VALID and READY are high
at the same rising clock edge.

Detects:
- Handshake completions (all 5 channels)
- Protocol violations (WVALID before AWVALID handshake, etc.)
- Timeout: VALID asserted for > timeout_clocks without READY
- Outstanding transaction count
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AXITransaction:
    txn_id: str
    channel: str
    addr: str | None
    start_time_ns: float
    end_time_ns: float | None
    status: str   # "ok" | "timeout" | "violation"
    notes: list[str] = field(default_factory=list)


@dataclass
class AXIViolation:
    time_ns: float
    channel: str
    violation_type: str
    message: str
    suggestion: str


def _get_value_at(tv: list[dict], time_ns: float) -> str | None:
    """Get the signal value at a given time (last value before or at time_ns)."""
    val = None
    for event in tv:
        if event["time"] <= time_ns:
            val = event["value"]
        else:
            break
    return val


def _find_clock_edges(clock_tv: list[dict]) -> list[float]:
    """Return timestamps of all rising clock edges."""
    edges: list[float] = []
    for i in range(1, len(clock_tv)):
        if clock_tv[i - 1]["value"] in ("0", "x") and clock_tv[i]["value"] == "1":
            edges.append(clock_tv[i]["time"])
    return edges


def decode_axi(
    signal_events: dict[str, list[dict]],  # name → compressed events
    axi_prefix: str = "",
    clock_name: str | None = None,
    timeout_cycles: int = 100,
) -> dict:
    """Decode AXI4 transactions and detect protocol violations.

    Args:
        signal_events: Compressed signal events from waveform-mcp compressor.
        axi_prefix:    Signal name prefix, e.g. "m_axi_" for m_axi_awvalid.
        clock_name:    Clock signal name for edge sampling (auto-detected if None).
        timeout_cycles: Cycles before flagging a VALID without READY as timeout.

    Returns:
        {
            "transactions":  [AXITransaction as dict],
            "violations":    [AXIViolation as dict],
            "summary":       str,
        }
    """
    pfx = axi_prefix

    def sig(name: str) -> list[dict]:
        return signal_events.get(f"{pfx}{name}", [])

    # Auto-detect clock
    if clock_name is None:
        for name in signal_events:
            if "clk" in name.lower() or "clock" in name.lower():
                clock_name = name
                break

    clock_tv = signal_events.get(clock_name or "", [])
    clock_edges = _find_clock_edges(clock_tv) if clock_tv else []

    if not clock_edges:
        return {
            "transactions": [],
            "violations":   [],
            "summary": f"No clock edges found ({clock_name or 'no clock detected'}). Cannot decode AXI transactions.",
        }

    transactions: list[dict] = []
    violations: list[dict] = []

    # ── Detect handshakes on each channel ─────────────────────────────────────
    channels = [
        ("aw", "awvalid", "awready"),
        ("w",  "wvalid",  "wready"),
        ("b",  "bvalid",  "bready"),
        ("ar", "arvalid", "arready"),
        ("r",  "rvalid",  "rready"),
    ]

    channel_handshakes: dict[str, list[float]] = {}

    for ch, valid_name, ready_name in channels:
        valid_tv = sig(valid_name)
        ready_tv = sig(ready_name)

        if not valid_tv:
            continue

        handshakes: list[float] = []
        valid_assert_time: float | None = None
        valid_cycle_count = 0

        for edge_time in clock_edges:
            valid_val = _get_value_at(valid_tv, edge_time)
            ready_val = _get_value_at(ready_tv, edge_time) if ready_tv else None

            if valid_val == "1":
                if valid_assert_time is None:
                    valid_assert_time = edge_time
                valid_cycle_count += 1

                if ready_val == "1":
                    handshakes.append(edge_time)
                    valid_assert_time = None
                    valid_cycle_count = 0
                elif valid_cycle_count > timeout_cycles:
                    violations.append({
                        "time_ns":       edge_time,
                        "channel":       ch.upper(),
                        "violation_type": "handshake_timeout",
                        "message": (
                            f"{pfx}{valid_name} asserted for >{timeout_cycles} cycles "
                            f"without {pfx}{ready_name} response"
                        ),
                        "suggestion":    "Check if slave is stalled or READY is driven correctly",
                    })
                    valid_assert_time = None
                    valid_cycle_count = 0
            else:
                valid_assert_time = None
                valid_cycle_count = 0

        channel_handshakes[ch] = handshakes

    # ── AXI ordering violation: W before AW ───────────────────────────────────
    aw_times = channel_handshakes.get("aw", [])
    w_times  = channel_handshakes.get("w", [])

    for w_time in w_times:
        preceding_aw = [t for t in aw_times if t <= w_time]
        if not preceding_aw:
            violations.append({
                "time_ns":       w_time,
                "channel":       "W",
                "violation_type": "w_before_aw",
                "message": f"Write data handshake at {w_time:.1f}ns before any write address handshake",
                "suggestion":    "Ensure AWVALID/AWREADY completes before or concurrently with WVALID/WREADY",
            })

    # ── Build transaction records (AW→W→B triples) ────────────────────────────
    b_times = channel_handshakes.get("b", [])
    for i, aw_time in enumerate(aw_times):
        next_aw = aw_times[i + 1] if i + 1 < len(aw_times) else float("inf")
        # W handshakes between this AW and the next
        matching_w = [t for t in w_times if aw_time <= t < next_aw]
        # B handshake after AW
        matching_b = [t for t in b_times if t > aw_time]
        b_time = matching_b[0] if matching_b else None

        addr_sig = sig("awaddr")
        addr_val = _get_value_at(addr_sig, aw_time) if addr_sig else None

        transactions.append({
            "txn_id":     f"write_{i}",
            "type":       "write",
            "aw_time_ns": aw_time,
            "w_times_ns": matching_w,
            "b_time_ns":  b_time,
            "addr":       addr_val,
            "status":     "ok" if b_time else "no_response",
        })

    ar_times = channel_handshakes.get("ar", [])
    r_times  = channel_handshakes.get("r", [])
    for i, ar_time in enumerate(ar_times):
        next_ar = ar_times[i + 1] if i + 1 < len(ar_times) else float("inf")
        matching_r = [t for t in r_times if ar_time < t < next_ar]

        addr_sig = sig("araddr")
        addr_val = _get_value_at(addr_sig, ar_time) if addr_sig else None

        transactions.append({
            "txn_id":     f"read_{i}",
            "type":       "read",
            "ar_time_ns": ar_time,
            "r_times_ns": matching_r,
            "addr":       addr_val,
            "status":     "ok" if matching_r else "no_response",
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    writes = sum(1 for t in transactions if t["type"] == "write")
    reads  = sum(1 for t in transactions if t["type"] == "read")
    summary = (
        f"AXI decode: {writes} write transactions, {reads} read transactions, "
        f"{len(violations)} protocol violation(s) on prefix '{pfx}'"
    )

    return {
        "transactions": transactions,
        "violations":   violations,
        "summary":      summary,
    }
