"""Tier 2 — pure-logic unit tests for ``classify_differences_tool``.

Drives the 7-category diff classifier with synthetic numpy signals and
asserts that each category's detector fires on its canonical pattern.
Entirely offline — no MCP spawn, no LLM calls, no VISA.

Category → detector mapping (see server.py docstring):
    overshoot    → rise_time_overshoot
    dc_offset    → dc_offset
    amplitude    → amplitude_error
    noise        → hf_noise_emi
    drift        → freq_deviation_ppm / systematic_offset
    phase        → phase_shift
    unknown      → missing_logic_event / unclassified
"""

from __future__ import annotations

import asyncio

import numpy as np

from instrument_mcp.server import classify_differences_tool


def _time_axis(n: int, dt_ns: float = 1.0) -> list[float]:
    return [i * dt_ns for i in range(n)]


# ── 1. Overshoot at edges ────────────────────────────────────────────────────
def test_detects_overshoot_at_edges():
    n = 200
    sim = np.zeros(n)
    sim[100:] = 1.0                      # single rising edge: sim[99]→sim[100]
    meas = sim.copy()
    # The detector scans np.diff(sim) (length n-1), so edges live at index 99.
    # Put the spike at 99 so it lines up with the detector's edge candidate.
    meas[99] = 0.8                       # overshoot spike exactly on the edge
    meas[98] = 0.3                       # modest pre-edge ring
    diff = (meas - sim).tolist()

    findings = asyncio.run(classify_differences_tool(
        diff_v=diff,
        time_ns=_time_axis(n),
        sim_v=sim.tolist(),
        meas_v=meas.tolist(),
    ))
    types = {f["diff_type"] for f in findings}
    assert "rise_time_overshoot" in types, f"Expected rise_time_overshoot in {types}"


# ── 2. DC offset (mild, "suspicious" tier) ───────────────────────────────────
def test_detects_dc_offset():
    n = 300
    rng = np.random.default_rng(1)
    sim = rng.normal(0.0, 0.01, n)
    meas = sim + 0.05                    # small uniform offset below 0.1
    diff = (meas - sim).tolist()

    findings = asyncio.run(classify_differences_tool(
        diff_v=diff,
        time_ns=_time_axis(n),
        sim_v=sim.tolist(),
        meas_v=meas.tolist(),
    ))
    types = {f["diff_type"] for f in findings}
    assert "dc_offset" in types


# ── 3. Amplitude error (large offset, "anomalous" tier) ──────────────────────
def test_detects_amplitude_error():
    n = 300
    rng = np.random.default_rng(2)
    sim = rng.normal(0.0, 0.01, n)
    meas = sim + 0.5                     # well above the 0.1 threshold
    diff = (meas - sim).tolist()

    findings = asyncio.run(classify_differences_tool(
        diff_v=diff,
        time_ns=_time_axis(n),
        sim_v=sim.tolist(),
        meas_v=meas.tolist(),
    ))
    amp = [f for f in findings if f["diff_type"] == "amplitude_error"]
    assert amp, f"Expected amplitude_error in {findings}"
    assert amp[0]["classification"] == "anomalous"


# ── 4. HF noise / EMI ────────────────────────────────────────────────────────
def test_detects_hf_noise():
    n = 512
    dt_ns = 0.05                         # 20 GHz sample rate
    t = np.arange(n) * dt_ns
    # Clock at 10 MHz; noise deliberately well above 5× clock (50 MHz).
    sim = np.zeros(n)
    noise = 0.3 * np.sin(2 * np.pi * 500e6 * (t * 1e-9))  # 500 MHz tone
    meas = sim + noise
    diff = (meas - sim).tolist()

    findings = asyncio.run(classify_differences_tool(
        diff_v=diff,
        time_ns=t.tolist(),
        sim_v=sim.tolist(),
        meas_v=meas.tolist(),
        clock_period_ns=100.0,            # 10 MHz clock
    ))
    types = {f["diff_type"] for f in findings}
    assert "hf_noise_emi" in types


# ── 5. Timing / frequency drift ──────────────────────────────────────────────
def test_detects_timing_drift():
    n = 400
    t = np.arange(n, dtype=float)
    # Linear diff across the whole capture → pure phase drift.
    sim = np.zeros(n)
    meas = 0.002 * t
    diff = (meas - sim).tolist()

    findings = asyncio.run(classify_differences_tool(
        diff_v=diff,
        time_ns=t.tolist(),
        sim_v=sim.tolist(),
        meas_v=meas.tolist(),
    ))
    types = {f["diff_type"] for f in findings}
    # Depending on rms_diff threshold: either freq_deviation_ppm (expected)
    # or systematic_offset (suspicious); either counts as the "drift" category.
    assert types & {"freq_deviation_ppm", "systematic_offset"}, (
        f"Expected a drift-family finding, got {types}"
    )


# ── 6. Phase shift (shape matches after small shift) ─────────────────────────
def test_detects_phase_shift():
    n = 200
    t = np.arange(n, dtype=float)
    # Square-ish wave with two edges; measurement is identical but lags by 5 samples.
    sim = np.zeros(n)
    sim[50:100] = 1.0
    sim[150:200] = 1.0
    shift = 5
    meas = np.zeros(n)
    meas[50 + shift:100 + shift] = 1.0
    meas[150 + shift:200] = 1.0          # truncate tail rather than wrap
    diff = (meas - sim).tolist()

    findings = asyncio.run(classify_differences_tool(
        diff_v=diff,
        time_ns=t.tolist(),
        sim_v=sim.tolist(),
        meas_v=meas.tolist(),
    ))
    types = {f["diff_type"] for f in findings}
    assert "phase_shift" in types, f"Expected phase_shift in {types}"


# ── 7. Unknown / missing events (anomalous tier) ─────────────────────────────
def test_detects_missing_events():
    n = 400
    t = np.arange(n, dtype=float)
    # Simulation has 4 pulses; measurement has zero (gate stuck low).
    sim = np.zeros(n)
    for start in (20, 100, 200, 300):
        sim[start:start + 20] = 1.0
    meas = np.zeros(n)
    diff = (meas - sim).tolist()

    findings = asyncio.run(classify_differences_tool(
        diff_v=diff,
        time_ns=t.tolist(),
        sim_v=sim.tolist(),
        meas_v=meas.tolist(),
    ))
    types = {f["diff_type"] for f in findings}
    assert "missing_logic_event" in types
    evt = next(f for f in findings if f["diff_type"] == "missing_logic_event")
    assert evt["classification"] == "anomalous"


# ── Empty / trivial inputs — sanity checks ───────────────────────────────────
def test_empty_returns_empty():
    findings = asyncio.run(classify_differences_tool(
        diff_v=[], time_ns=[], sim_v=[], meas_v=[],
    ))
    assert findings == []


def test_near_zero_diff_is_expected():
    n = 50
    sim = [0.0] * n
    meas = [0.0] * n
    findings = asyncio.run(classify_differences_tool(
        diff_v=[0.0] * n,
        time_ns=_time_axis(n),
        sim_v=sim,
        meas_v=meas,
    ))
    assert len(findings) == 1
    assert findings[0]["classification"] == "expected"
