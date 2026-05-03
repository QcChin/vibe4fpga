"""instrument-mcp — Measurement Instrument Access MCP Server.

Exposes 7 I/O + analysis tools plus 1 LLM-backed skill tool:

Waveform I/O (no LLM required):
    read_csv_waveform           — parse oscilloscope CSV export
    compute_fft_tool            — spectral analysis of a CSV waveform
    list_visa_instruments       — enumerate VISA-accessible instruments
    connect_instrument          — SCPI *IDN? handshake
    capture_live_waveform       — live-capture a waveform via SCPI
    align_with_simulation       — cross-correlate sim (VCD) vs. measurement
    classify_differences_tool   — 7-category diff classifier (pure numpy)

LLM-backed skill:
    analyze_instrument_diff     — instrument_analyze: classify + root-cause
                                  attribute a sim-vs-real waveform comparison.
"""

from __future__ import annotations

import logging

import numpy as np
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .aligner import compute_fft, cross_correlate_align
from .readers.generic import read_csv_auto
from .readers.rigol import read_rigol_csv
from .readers.scpi import capture_waveform, connect, list_instruments
from .skills.instrument_analyze.skill import run as instrument_analyze_run

logger = logging.getLogger(__name__)

mcp = FastMCP("instrument-mcp")


# ═════════════════════════════════════════════════════════════════════════════
# File reading tools
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def read_csv_waveform(
    file_path: str,
    vendor: str = "auto",
    channel: int = 1,
) -> dict:
    """Parse oscilloscope CSV export.

    Args:
        file_path: Path to CSV file.
        vendor:    "auto" | "rigol" | "generic"
        channel:   Channel number (1-based).

    Returns:
        {time_ns, voltage_v, sample_rate_hz, duration_ns, vendor, points}
    """
    ch0 = channel - 1   # 0-based for generic reader

    if vendor == "rigol":
        return read_rigol_csv(file_path, channel=f"CH{channel}")

    if vendor == "auto":
        # Heuristic: peek at file content for Rigol '#' metadata markers
        try:
            with open(file_path, "r", errors="replace") as f:
                head = f.read(256)
            if "#Model" in head or "#Channel" in head or "#SampleRate" in head:
                return read_rigol_csv(file_path, channel=f"CH{channel}")
        except OSError:
            pass

    return read_csv_auto(file_path, channel=ch0)


@mcp.tool()
async def compute_fft_tool(
    file_path: str,
    vendor: str = "auto",
    channel: int = 1,
    n_points: int = 1024,
) -> dict:
    """Compute FFT of an oscilloscope waveform for spectral analysis.

    Returns:
        {freq_mhz, amplitude, dominant_freq_mhz, thd_pct, sample_rate_mhz}
    """
    waveform = await read_csv_waveform(file_path, vendor=vendor, channel=channel)
    if "error" in waveform:
        return waveform

    return compute_fft(
        time_ns=waveform["time_ns"],
        voltage_v=waveform["voltage_v"],
        n_points=n_points,
    )


# ═════════════════════════════════════════════════════════════════════════════
# SCPI live capture tools
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_visa_instruments() -> list[str]:
    """List all VISA-accessible instruments on the system.

    Returns an empty list if no VISA backend (NI-VISA / Keysight / pyvisa-py)
    is installed. See the README "Windows notes" section for driver install
    pointers.
    """
    return list_instruments()


@mcp.tool()
async def connect_instrument(resource_string: str, timeout_ms: int = 5000) -> dict:
    """Open a VISA connection and identify the instrument.

    Args:
        resource_string: VISA resource, e.g. "TCPIP0::192.168.1.5::INSTR"
        timeout_ms:      Query timeout in milliseconds.

    Returns:
        {"connected": bool, "idn": str}
    """
    return connect(resource_string, timeout_ms=timeout_ms)


@mcp.tool()
async def capture_live_waveform(
    resource_string: str,
    channel: int = 1,
    timeout_ms: int = 10000,
) -> dict:
    """Capture a live waveform directly from an instrument via SCPI.

    Uses :WAVeform: subsystem (Rigol/Keysight/SIGLENT) or
    SAVe:WAVEform CSV fallback (Tektronix).

    Args:
        resource_string: VISA resource string.
        channel:         Channel number (1-based).

    Returns:
        {time_ns, voltage_v, sample_rate_hz, duration_ns, idn}
    """
    return capture_waveform(resource_string, channel=channel, timeout_ms=timeout_ms)


# ═════════════════════════════════════════════════════════════════════════════
# Alignment and comparison tools
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def align_with_simulation(
    meas_file: str,
    sim_vcd_file: str,
    sim_signal: str,
    meas_channel: int = 1,
    meas_vendor: str = "auto",
    max_shift_ns: float | None = None,
) -> dict:
    """Time-domain alignment of measurement vs. simulation waveform.

    Uses cross-correlation:  Δt = argmax(xcorr(meas, sim))

    Args:
        meas_file:    Path to oscilloscope CSV file.
        sim_vcd_file: Path to VCD simulation file.
        sim_signal:   Signal name in the VCD file to compare.
        meas_channel: Oscilloscope channel number (1-based).
        max_shift_ns: Maximum allowed alignment shift (None = unlimited).

    Returns:
        {
            offset_ns, offset_samples, correlation_peak,
            aligned_meas_v, sim_v_aligned, time_ns, diff_v
        }
    """
    # Load measurement
    meas = await read_csv_waveform(meas_file, vendor=meas_vendor, channel=meas_channel)
    if "error" in meas:
        return meas

    # Load simulation signal from VCD
    try:
        from waveform_mcp.parser import parse_waveform as parse_vcd
        vcd_meta = parse_vcd(sim_vcd_file)
        sig = vcd_meta.signals.get(sim_signal)
        if sig is None:
            # Try partial match
            matches = [n for n in vcd_meta.signals if sim_signal in n]
            if not matches:
                return {"error": f"Signal '{sim_signal}' not found in {sim_vcd_file}"}
            sig = vcd_meta.signals[matches[0]]

        sim_tv = sig.tv
        sim_time_ns = [t for t, _ in sim_tv]
        # Convert binary/hex values to float
        sim_voltage: list[float] = []
        for _, v in sim_tv:
            try:
                sim_voltage.append(float(int(v, 2)) if set(v).issubset("01xz") else float(v))
            except (ValueError, TypeError):
                sim_voltage.append(0.0)

    except ImportError:
        return {"error": "waveform_mcp not available — run waveform-mcp as a sidecar"}

    return cross_correlate_align(
        sim_time_ns=sim_time_ns,
        sim_voltage=sim_voltage,
        meas_time_ns=meas["time_ns"],
        meas_voltage=meas["voltage_v"],
        max_shift_ns=max_shift_ns,
    )


# ── Private difference detectors (one per category, pure numpy, no LLM) ──────

def _detect_rise_time_overshoot(diff, sim, rms_diff: float) -> dict | None:
    """Detect overshoot / rise-time mismatch at signal edges (expected)."""
    sim_edges = np.where(np.abs(np.diff(sim)) > 0.4)[0]
    if len(sim_edges) == 0:
        return None
    edge_diffs = [abs(diff[i]) for i in sim_edges if i < len(diff)]
    if edge_diffs and max(edge_diffs) > rms_diff * 2:
        return {
            "diff_type":      "rise_time_overshoot",
            "classification": "expected",
            "evidence":       f"Large diff ({max(edge_diffs):.3f}) at {len(sim_edges)} edge(s); near-zero in stable regions",
            "suggestion":     "RTL correct — physical effect. Consider adjusting DRIVE strength or output impedance.",
        }
    return None


def _detect_dc_offset(diff, rms_diff: float) -> dict | None:
    """Detect systematic DC / amplitude offset (suspicious or anomalous)."""
    dc_offset = float(np.mean(diff))
    if abs(dc_offset) > rms_diff * 0.5:
        return {
            "diff_type":      "amplitude_error" if abs(dc_offset) > 0.1 else "dc_offset",
            "classification": "anomalous" if abs(dc_offset) > 0.1 else "suspicious",
            "evidence":       f"Mean diff = {dc_offset:.4f} (systematic amplitude offset)",
            "suggestion":     "Check data path bit-width, truncation, or shift operations (e.g. >> mismatch).",
        }
    return None


def _detect_hf_noise(t, diff, clock_period_ns: float) -> dict | None:
    """Detect high-frequency noise / EMI overlay (expected)."""
    fft_result = compute_fft(list(t), list(diff), n_points=512)
    if "dominant_freq_mhz" not in fft_result:
        return None
    dom_freq = fft_result["dominant_freq_mhz"]
    clock_freq_mhz = 1000.0 / clock_period_ns if clock_period_ns > 0 else 100.0
    if dom_freq > clock_freq_mhz * 5:
        return {
            "diff_type":      "hf_noise_emi",
            "classification": "expected",
            "evidence":       f"Dominant diff frequency {dom_freq:.1f} MHz >> clock ({clock_freq_mhz:.1f} MHz)",
            "suggestion":     "PCB routing or power supply issue — not an RTL problem.",
        }
    return None


def _detect_timing_drift(t, diff, rms_diff: float) -> dict | None:
    """Detect linear frequency drift or systematic timing offset (suspicious)."""
    if len(t) <= 10:
        return None
    t_norm = (t - t[0]) / max(t[-1] - t[0], 1.0)
    poly = np.polyfit(t_norm, diff, 1)
    linear_residual = float(np.std(diff - np.polyval(poly, t_norm)))
    if abs(poly[0]) > rms_diff * 0.3 and linear_residual < rms_diff * 0.5:
        return {
            "diff_type":      "freq_deviation_ppm",
            "classification": "expected",
            "evidence":       f"Linear phase drift detected (slope={poly[0]:.4f})",
            "suggestion":     "Crystal oscillator frequency tolerance — no RTL change needed.",
        }
    if abs(float(np.mean(diff[:max(len(diff) // 4, 1)]))) > rms_diff * 1.5:
        return {
            "diff_type":      "systematic_offset",
            "classification": "suspicious",
            "evidence":       "Uniform advance/delay across waveform",
            "suggestion":     "Check pipeline stage count or clock division ratio in RTL.",
        }
    return None


def _detect_phase_shift(sim, meas, rms_diff: float) -> dict | None:
    """Detect a correct-shape-but-shifted-in-phase waveform (suspicious).

    Correlation between shifted sim and meas remains high despite a large
    pointwise RMS diff → classic phase offset of a few samples.
    """
    if len(sim) < 4 or len(meas) < 4 or len(sim) != len(meas):
        return None
    best_corr = -1.0
    best_shift = 0
    base_norm = float(np.sqrt(np.sum(sim ** 2) * np.sum(meas ** 2)) + 1e-12)
    span = max(2, len(sim) // 10)
    for shift in range(-span, span + 1):
        if shift == 0:
            continue
        if shift > 0:
            a = sim[:-shift]
            b = meas[shift:]
        else:
            a = sim[-shift:]
            b = meas[:shift]
        if len(a) < 2:
            continue
        corr = float(np.sum(a * b) / base_norm)
        if corr > best_corr:
            best_corr = corr
            best_shift = shift
    if best_corr > 0.85 and abs(best_shift) > 0 and rms_diff > 0.05:
        return {
            "diff_type":      "phase_shift",
            "classification": "suspicious",
            "evidence":       f"Shape matches after shifting by {best_shift} samples (corr={best_corr:.3f})",
            "suggestion":     "Check sampling alignment or pipeline latency in RTL.",
        }
    return None


def _detect_missing_events(sim, meas) -> dict | None:
    """Detect missing logic pulses in measurement vs. simulation (anomalous)."""
    sim_pulses  = int(np.sum(np.diff((sim > 0.5).astype(int)) > 0))
    meas_pulses = int(np.sum(np.diff((meas > 0.5).astype(int)) > 0))
    if sim_pulses > 0 and meas_pulses < sim_pulses * 0.7:
        missing = sim_pulses - meas_pulses
        return {
            "diff_type":      "missing_logic_event",
            "classification": "anomalous",
            "evidence":       f"Simulation has {sim_pulses} pulses; measurement has {meas_pulses} ({missing} missing)",
            "suggestion":     "Check constraint file, enable conditions, or use ILA to verify in-system behavior.",
        }
    return None


@mcp.tool()
async def classify_differences_tool(
    diff_v: list[float],
    time_ns: list[float],
    sim_v: list[float],
    meas_v: list[float],
    clock_period_ns: float = 10.0,
) -> list[dict]:
    """Classify simulation vs. real-measurement differences into 7 categories.

    Categories covered:
      * overshoot   (``rise_time_overshoot``)       — expected
      * dc_offset   (``dc_offset``)                 — suspicious
      * amplitude   (``amplitude_error``)           — anomalous
      * noise       (``hf_noise_emi``)              — expected
      * drift       (``freq_deviation_ppm`` /
                      ``systematic_offset``)        — expected / suspicious
      * phase       (``phase_shift``)               — suspicious
      * unknown     (``missing_logic_event`` /
                      ``unclassified``)             — anomalous / suspicious

    Args:
        diff_v:          Point-by-point difference (meas_aligned - sim).
        time_ns:         Unified time axis.
        sim_v:           Simulation values on unified grid.
        meas_v:          Aligned measurement values.
        clock_period_ns: Clock period for edge-width analysis.

    Returns:
        [{diff_type, classification, evidence, suggestion}]
    """
    if not diff_v or not time_ns:
        return []

    diff = np.array(diff_v)
    sim  = np.array(sim_v)
    meas = np.array(meas_v)
    t    = np.array(time_ns)

    rms_diff = float(np.sqrt(np.mean(diff ** 2)))
    if rms_diff < 1e-6:
        return [{"diff_type": "none", "classification": "expected",
                 "evidence": "RMS diff ≈ 0", "suggestion": "Simulation matches measurement"}]

    findings: list[dict] = []
    for detector_result in [
        _detect_rise_time_overshoot(diff, sim, rms_diff),
        _detect_dc_offset(diff, rms_diff),
        _detect_hf_noise(t, diff, clock_period_ns),
        _detect_timing_drift(t, diff, rms_diff),
        _detect_phase_shift(sim, meas, rms_diff),
        _detect_missing_events(sim, meas),
    ]:
        if detector_result is not None:
            findings.append(detector_result)

    if not findings:
        findings.append({
            "diff_type":      "unclassified",
            "classification": "suspicious",
            "evidence":       f"RMS diff = {rms_diff:.4f}, no clear pattern",
            "suggestion":     "Manual inspection recommended.",
        })

    return findings


# ═════════════════════════════════════════════════════════════════════════════
# LLM-backed skill — wraps instrument_analyze.skill.run behind a pydantic tool.
# ═════════════════════════════════════════════════════════════════════════════

class AnalyzeInstrumentDiffInput(BaseModel):
    """Inputs for ``analyze_instrument_diff``.

    The tool can be driven in two ways:

    1. **Raw arrays** — pass ``oscilloscope_data`` + ``simulation_data`` as
       ``list[float]`` and the tool will align, classify and attribute.
    2. **File paths** — pass a string for ``oscilloscope_data`` (CSV path or
       VISA resource string if it looks like one) and ``simulation_data``
       (a path to a VCD file) + ``sim_signal``.
    """

    oscilloscope_data: list[float] | str = Field(
        ...,
        description=(
            "Either a list of measurement voltages sampled on the time axis "
            "implied by ``clock_period_ns`` and ``meas_channel``, or a path "
            "to an oscilloscope CSV export, or a VISA resource string for "
            "live SCPI capture (must start with 'TCPIP', 'USB', or 'GPIB')."
        ),
    )
    simulation_data: list[float] | str = Field(
        ...,
        description=(
            "Either a list of simulation voltages (must match length of "
            "``oscilloscope_data`` when both are lists) or a path to a VCD "
            "file whose signal ``sim_signal`` will be loaded."
        ),
    )
    sim_signal: str = Field(
        "",
        description=(
            "Signal name inside the VCD file. Required when ``simulation_data`` "
            "is a VCD path; ignored when ``simulation_data`` is a list."
        ),
    )
    meas_channel:    int   = Field(1,    description="Oscilloscope channel number (1-based).")
    meas_vendor:     str   = Field("auto", description='CSV vendor hint: "auto" | "rigol" | "generic".')
    clock_period_ns: float = Field(10.0, description="Clock period (ns) used by the diff classifier.")
    model:           str   = Field("claude", description="LLM model key (see vibe4fpga-llm-client registry).")
    timeout_ms:      int   = Field(10000, description="SCPI capture timeout (ignored for file inputs).")


def _looks_like_visa_resource(s: str) -> bool:
    upper = s.strip().upper()
    return upper.startswith(("TCPIP", "USB", "GPIB", "ASRL"))


@mcp.tool()
async def analyze_instrument_diff(inputs: AnalyzeInstrumentDiffInput) -> dict:
    """Compare an oscilloscope capture against simulation data and root-cause the diff.

    Runs the ``instrument_analyze`` skill: align → classify into 7 categories
    → LLM attribution. When the inputs are raw ``list[float]`` arrays the
    alignment + classification run directly on them without touching the
    filesystem; when they are paths the loaders in
    :mod:`instrument_mcp.readers` and :mod:`waveform_mcp.parser` are used
    (the latter requires the waveform-mcp package to be importable).

    Returns:
        ``{alignment, diff_findings, summary, llm_analysis, report_md}``.
    """
    osc = inputs.oscilloscope_data
    sim = inputs.simulation_data

    # ── Raw-arrays branch — do the align+classify here, then hand an already
    # populated ``alignment`` + ``diff_findings`` pair to the skill so it only
    # runs the LLM attribution + report stage. ───────────────────────────────
    if isinstance(osc, list) and isinstance(sim, list):
        if not osc or not sim:
            return {"error": "oscilloscope_data and simulation_data must be non-empty lists"}
        # Build a shared time axis assuming sample index * clock_period_ns.
        length = min(len(osc), len(sim))
        dt_ns = max(inputs.clock_period_ns, 1e-6)
        time_ns = [i * dt_ns for i in range(length)]
        osc_trim = osc[:length]
        sim_trim = sim[:length]
        alignment = cross_correlate_align(
            sim_time_ns=time_ns,
            sim_voltage=sim_trim,
            meas_time_ns=time_ns,
            meas_voltage=osc_trim,
        )
        diff_findings = await classify_differences_tool(
            diff_v=alignment["diff_v"],
            time_ns=alignment["time_ns"],
            sim_v=alignment["sim_v_aligned"],
            meas_v=alignment["aligned_meas_v"],
            clock_period_ns=inputs.clock_period_ns,
        )
        return await instrument_analyze_run(
            sim_signal=inputs.sim_signal or "raw_array",
            clock_period_ns=inputs.clock_period_ns,
            model=inputs.model,
            alignment=alignment,
            diff_findings=diff_findings,
        )

    # ── File / resource branch ───────────────────────────────────────────────
    if not isinstance(osc, str) or not isinstance(sim, str):
        return {
            "error": (
                "oscilloscope_data and simulation_data must be either both "
                "list[float] or both strings (paths / resource)."
            )
        }

    kwargs: dict = {
        "sim_vcd_file":    sim,
        "sim_signal":      inputs.sim_signal,
        "meas_channel":    inputs.meas_channel,
        "meas_vendor":     inputs.meas_vendor,
        "clock_period_ns": inputs.clock_period_ns,
        "model":           inputs.model,
        "timeout_ms":      inputs.timeout_ms,
    }
    if _looks_like_visa_resource(osc):
        kwargs["scpi_resource"] = osc
    else:
        kwargs["meas_file"] = osc

    return await instrument_analyze_run(**kwargs)


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Console-script entry point for ``instrument-mcp``."""
    mcp.run()


if __name__ == "__main__":   # pragma: no cover
    main()
