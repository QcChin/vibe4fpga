"""Tier 2 pure-logic unit tests for the waveform_debug detectors.

No LLM calls, no MCP handshake — exercises three of the five deterministic
detectors directly against synthesized event dictionaries. The ``Anomaly``
dataclass contract is also pinned so downstream serialization in
``skill.run`` cannot drift silently.
"""

from __future__ import annotations

import asyncio

from waveform_mcp.skills.waveform_debug.detectors import (
    Anomaly,
    detect_glitches,
    detect_handshake_timeouts,
    run_all_detectors,
    track_xz_states,
)


# ── 1. GlitchDetector ─────────────────────────────────────────────────────────

def test_glitch_detector_flags_narrow_pulse() -> None:
    """A 0 → 1 → 0 pulse narrower than one clock period is a glitch."""
    clock_period_ns = 10.0
    signal_events = {
        "data_out": [
            {"time":  0.0, "value": "0"},
            {"time": 12.0, "value": "1"},   # rising edge
            {"time": 14.0, "value": "0"},   # 2 ns pulse → well below 10 ns period
            {"time": 50.0, "value": "0"},   # sentinel
        ]
    }
    anomalies = detect_glitches(signal_events, clock_period_ns)
    assert len(anomalies) == 1
    a = anomalies[0]
    assert isinstance(a, Anomaly)
    assert a.detector == "GlitchDetector"
    assert a.severity == "warning"
    assert a.signal == "data_out"
    assert a.anomaly_type == "glitch"


def test_glitch_detector_ignores_full_width_pulse() -> None:
    """Pulses at least one clock period wide are NOT glitches."""
    clock_period_ns = 10.0
    signal_events = {
        "data_out": [
            {"time":  0.0, "value": "0"},
            {"time": 10.0, "value": "1"},
            {"time": 30.0, "value": "0"},   # 20 ns pulse → 2 cycles wide
        ]
    }
    assert detect_glitches(signal_events, clock_period_ns) == []


def test_glitch_detector_returns_empty_for_zero_clock() -> None:
    """Degenerate clock period short-circuits to an empty list (no div-by-zero)."""
    assert detect_glitches({"x": [{"time": 0, "value": "0"}]}, 0.0) == []


# ── 2. XZStateTracker ─────────────────────────────────────────────────────────

def test_xz_tracker_flags_x_entry() -> None:
    """A signal entering X at some point produces one error Anomaly per entry."""
    signal_events = {
        "reg_q": [
            {"time":  0.0, "value": "0"},
            {"time": 10.0, "value": "x"},   # enters X
            {"time": 20.0, "value": "1"},   # leaves X
            {"time": 30.0, "value": "x"},   # re-enters X
        ]
    }
    anomalies = track_xz_states(signal_events)
    # Two entries into X → two anomalies.
    assert len(anomalies) == 2
    assert all(a.detector == "XZStateTracker" for a in anomalies)
    assert all(a.severity == "error" for a in anomalies)
    assert {a.anomaly_type for a in anomalies} == {"x_state"}


def test_xz_tracker_ignores_clean_signal() -> None:
    """A signal that never enters X/Z yields no anomalies."""
    signal_events = {
        "reg_q": [
            {"time":  0.0, "value": "0"},
            {"time": 10.0, "value": "1"},
            {"time": 20.0, "value": "0"},
        ]
    }
    assert track_xz_states(signal_events) == []


# ── 3. HandshakeTimeout ───────────────────────────────────────────────────────

def test_handshake_timeout_flags_stuck_valid() -> None:
    """A ``_valid`` signal asserted longer than ``timeout_cycles * period`` is stuck."""
    clock_period_ns = 10.0
    timeout_cycles  = 5   # stuck threshold = 50 ns
    signal_events = {
        "m_axi_awvalid": [
            {"time":   0.0, "value": "0"},
            {"time":  10.0, "value": "1"},
            {"time": 200.0, "value": "1"},   # still high 190 ns later → timeout
            {"time": 250.0, "value": "0"},
        ]
    }
    anomalies = detect_handshake_timeouts(
        signal_events,
        clock_period_ns=clock_period_ns,
        timeout_cycles=timeout_cycles,
    )
    assert len(anomalies) >= 1
    first = anomalies[0]
    assert first.detector == "HandshakeTimeout"
    assert first.severity == "warning"
    assert first.anomaly_type == "handshake_timeout"
    assert first.signal == "m_axi_awvalid"


def test_handshake_timeout_ignores_quick_deassertion() -> None:
    """A valid that de-asserts within the timeout window is clean."""
    signal_events = {
        "req_valid": [
            {"time":  0.0, "value": "0"},
            {"time": 10.0, "value": "1"},
            {"time": 20.0, "value": "0"},    # deasserted after 10 ns
        ]
    }
    assert detect_handshake_timeouts(signal_events, clock_period_ns=10.0, timeout_cycles=100) == []


# ── 4. run_all_detectors aggregation ──────────────────────────────────────────

def test_run_all_detectors_sorts_by_severity() -> None:
    """Errors must appear before warnings in the aggregated output."""
    signal_events = {
        "bus": [
            {"time":  0.0, "value": "0"},
            {"time": 12.0, "value": "1"},
            {"time": 14.0, "value": "0"},   # narrow glitch (warning)
        ],
        "reg_q": [
            {"time":  0.0, "value": "0"},
            {"time": 10.0, "value": "x"},   # X-state (error)
        ],
    }
    anomalies = asyncio.run(run_all_detectors(
        signal_events=signal_events,
        clock_period_ns=10.0,
        clock_names=[],
        axi_prefix="",
    ))
    # Errors come first (XZ), then warnings (glitch).
    severities = [a.severity for a in anomalies]
    assert "error" in severities
    assert "warning" in severities
    assert severities.index("error") < severities.index("warning")
