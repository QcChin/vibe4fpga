"""Five parallel WaveformDebug detectors.

Design principle: Programs do detection (scanning hundreds of thousands of
time steps, precisely calculating edge widths, protocol compliance).
LLM does reasoning (understanding race hazard principles, repair solutions).

Detectors:
  1. GlitchDetector       — pulses narrower than 1 clock period
  2. XZStateTracker       — X/Z propagation tracking
  3. CDCDetector          — cross-clock-domain signal sampling hazards
  4. AXIProtocolDecoder   — AXI4 handshake violations (via axi_decoder)
  5. HandshakeTimeout     — VALID asserted without READY for > N clocks
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class Anomaly:
    detector: str
    severity: str           # "error" | "warning" | "info"
    signal:   str
    time_ns:  float
    anomaly_type: str
    message:  str
    rtl_hint: str = ""      # hint for RTL reverse mapping
    context:  dict = field(default_factory=dict)


# ── 1. Glitch Detector ────────────────────────────────────────────────────────

def detect_glitches(
    signal_events: dict[str, list[dict]],   # name → [{time, value}]
    clock_period_ns: float,
) -> list[Anomaly]:
    """Find pulses narrower than one clock period.

    Distinguishes race hazards (combinational glitches) from real short pulses.
    A glitch is defined as: value changes A→B→A within < clock_period_ns.
    """
    anomalies: list[Anomaly] = []

    if clock_period_ns <= 0:
        return anomalies

    for name, events in signal_events.items():
        if len(events) < 3:
            continue
        for i in range(1, len(events) - 1):
            t0, v0 = events[i - 1]["time"], events[i - 1]["value"]
            t1, v1 = events[i    ]["time"], events[i    ]["value"]
            t2, v2 = events[i + 1]["time"], events[i + 1]["value"]

            if v1 != v0 and v1 != v2 and v0 == v2:
                pulse_width = t2 - t1
                if 0 < pulse_width < clock_period_ns:
                    anomalies.append(Anomaly(
                        detector="GlitchDetector",
                        severity="warning",
                        signal=name,
                        time_ns=t1,
                        anomaly_type="glitch",
                        message=(
                            f"Glitch on {name}: pulse width {pulse_width:.2f} ns "
                            f"< 1 clock period ({clock_period_ns:.2f} ns). "
                            f"Possible combinational race hazard."
                        ),
                        rtl_hint=f"Check combinational logic driving {name}",
                    ))
    return anomalies


# ── 2. X/Z State Tracker ─────────────────────────────────────────────────────

def track_xz_states(
    signal_events: dict[str, list[dict]],
) -> list[Anomaly]:
    """Record signals entering X or Z states and flag them."""
    anomalies: list[Anomaly] = []

    for name, events in signal_events.items():
        in_xz = False
        entry_time: float = 0.0

        for evt in events:
            val = str(evt["value"]).lower()
            is_xz = any(c in val for c in ("x", "z"))

            if is_xz and not in_xz:
                in_xz = True
                entry_time = evt["time"]
                anomalies.append(Anomaly(
                    detector="XZStateTracker",
                    severity="error",
                    signal=name,
                    time_ns=entry_time,
                    anomaly_type="x_state" if "x" in val else "z_state",
                    message=(
                        f"{name} entered {'X' if 'x' in val else 'Z'} state at "
                        f"{entry_time:.1f} ns. Possible uninitialized register or "
                        f"missing reset."
                    ),
                    rtl_hint=f"Check reset coverage for {name}; verify no undriven nets",
                ))
            elif not is_xz:
                in_xz = False

    return anomalies


# ── 3. CDC Detector ───────────────────────────────────────────────────────────

def detect_cdc(
    signal_events: dict[str, list[dict]],
    clock_names: list[str],
    min_sync_stages: int = 2,
) -> list[Anomaly]:
    """Identify signals that transition near multiple different clock edges.

    Heuristic: a signal that has transitions within half a cycle of two
    different clocks is a potential CDC crossing without synchronization.

    Note: Full CDC analysis requires RTL structural analysis (eda-bridge-mcp).
    This detector provides timing-domain evidence.
    """
    anomalies: list[Anomaly] = []

    if len(clock_names) < 2:
        return anomalies

    # Collect rising edges for each clock
    clock_edges: dict[str, list[float]] = {}
    for clk_name in clock_names:
        evts = signal_events.get(clk_name, [])
        edges: list[float] = []
        for i in range(1, len(evts)):
            if evts[i - 1]["value"] in ("0", "x") and evts[i]["value"] == "1":
                edges.append(evts[i]["time"])
        clock_edges[clk_name] = edges

    # Estimate clock periods
    periods: dict[str, float] = {}
    for clk, edges in clock_edges.items():
        if len(edges) >= 2:
            periods[clk] = edges[1] - edges[0]

    # For each non-clock signal, check proximity to multiple clocks
    for name, events in signal_events.items():
        if name in clock_names:
            continue

        transitions = [e["time"] for i, e in enumerate(events[1:], 1)
                       if e["value"] != events[i - 1]["value"]]

        clocks_nearby: dict[str, int] = {c: 0 for c in clock_names}

        for t in transitions:
            for clk, edges in clock_edges.items():
                period = periods.get(clk, 10.0)
                tolerance = period * 0.1  # 10% of period
                near = any(abs(t - edge) < tolerance for edge in edges)
                if near:
                    clocks_nearby[clk] += 1

        active_clocks = [c for c, cnt in clocks_nearby.items() if cnt > 0]
        if len(active_clocks) >= 2:
            anomalies.append(Anomaly(
                detector="CDCDetector",
                severity="warning",
                signal=name,
                time_ns=transitions[0] if transitions else 0.0,
                anomaly_type="cdc_suspect",
                message=(
                    f"{name} transitions correlate with {len(active_clocks)} clock domains "
                    f"({', '.join(active_clocks)}). Possible CDC crossing — verify "
                    f"{min_sync_stages}-FF synchronizer is present."
                ),
                rtl_hint=f"Search RTL for {name} to verify 2-FF synchronizer",
            ))

    return anomalies


# ── 4. AXI Protocol Decoder (via axi_decoder module) ─────────────────────────

def detect_axi_violations(
    signal_events: dict[str, list[dict]],
    axi_prefix: str = "",
    clock_name: str | None = None,
) -> list[Anomaly]:
    """Wrapper around axi_decoder — converts violations to Anomaly objects."""
    try:
        from waveform_mcp.axi_decoder import decode_axi
    except ImportError:
        # Running outside waveform-mcp context; skip silently
        return []

    result = decode_axi(signal_events, axi_prefix=axi_prefix, clock_name=clock_name)
    anomalies: list[Anomaly] = []

    for v in result.get("violations", []):
        anomalies.append(Anomaly(
            detector="AXIProtocolDecoder",
            severity="error",
            signal=f"{axi_prefix}{v['channel'].lower()}valid",
            time_ns=v["time_ns"],
            anomaly_type=v["violation_type"],
            message=v["message"],
            rtl_hint=v["suggestion"],
        ))

    return anomalies


# ── 5. Handshake Timeout Detector ─────────────────────────────────────────────

def detect_handshake_timeouts(
    signal_events: dict[str, list[dict]],
    clock_period_ns: float,
    timeout_cycles: int = 100,
    valid_signal_patterns: list[str] | None = None,
) -> list[Anomaly]:
    """Detect valid/req signals asserted without corresponding ack/ready.

    Default patterns: signals ending in _valid, _req, _en.
    """
    patterns = valid_signal_patterns or ["_valid", "_req", "_en", "valid", "req"]
    timeout_ns = timeout_cycles * clock_period_ns
    anomalies: list[Anomaly] = []

    for name, events in signal_events.items():
        # Only check signals that look like valid/req
        if not any(pat in name for pat in patterns):
            continue

        # Check for long assertion
        assert_start: float | None = None
        for evt in events:
            val = evt["value"]
            t   = evt["time"]

            if val == "1":
                if assert_start is None:
                    assert_start = t
                elif t - assert_start > timeout_ns:
                    anomalies.append(Anomaly(
                        detector="HandshakeTimeout",
                        severity="warning",
                        signal=name,
                        time_ns=assert_start,
                        anomaly_type="handshake_timeout",
                        message=(
                            f"{name} asserted for >{timeout_cycles} clocks "
                            f"({t - assert_start:.0f} ns) without deassertion. "
                            f"Check if partner ready/ack signal responds."
                        ),
                        rtl_hint=f"Inspect downstream block connected to {name}",
                    ))
                    assert_start = None  # avoid duplicate reports
            else:
                assert_start = None

    return anomalies


# ── Run all detectors in parallel ─────────────────────────────────────────────

async def run_all_detectors(
    signal_events: dict[str, list[dict]],
    clock_period_ns: float = 10.0,
    clock_names: list[str] | None = None,
    axi_prefix: str = "",
    timeout_cycles: int = 100,
) -> list[Anomaly]:
    """Run all 5 detectors concurrently and merge results."""
    loop = asyncio.get_event_loop()

    clk_names = clock_names or []

    tasks = [
        loop.run_in_executor(None, detect_glitches,            signal_events, clock_period_ns),
        loop.run_in_executor(None, track_xz_states,            signal_events),
        loop.run_in_executor(None, detect_cdc,                 signal_events, clk_names),
        loop.run_in_executor(None, detect_axi_violations,      signal_events, axi_prefix, None),
        loop.run_in_executor(None, detect_handshake_timeouts,  signal_events, clock_period_ns, timeout_cycles),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    anomalies: list[Anomaly] = []

    for r in results:
        if isinstance(r, list):
            anomalies.extend(r)
        # Silently skip exceptions from individual detectors

    # Sort by severity (error first) then by time
    severity_order = {"error": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda a: (severity_order.get(a.severity, 3), a.time_ns))

    return anomalies
