"""instrument-mcp — Measurement Instrument Access MCP Server."""

from __future__ import annotations

import logging

import numpy as np
from mcp.server.fastmcp import FastMCP

from .aligner import compute_fft, cross_correlate_align
from .readers.generic import read_csv_auto
from .readers.rigol import read_rigol_csv
from .readers.scpi import capture_waveform, connect, list_instruments

logger = logging.getLogger(__name__)

mcp = FastMCP("instrument-mcp")


# ── File reading tools ────────────────────────────────────────────────────────

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


# ── SCPI live capture tools ───────────────────────────────────────────────────

@mcp.tool()
async def list_visa_instruments() -> list[str]:
    """List all VISA-accessible instruments on the system."""
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


# ── Alignment and comparison tools ───────────────────────────────────────────

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


@mcp.tool()
# ── Private difference detectors (one per category) ──────────────────────────

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
    """Classify simulation vs. real-measurement differences.

    Categorizes differences as:
      expected  — physical effects (rise time, overshoot, noise, freq deviation)
      suspicious — may indicate design issues (systematic offset, nonlinear drift)
      anomalous — likely design issues (missing events, amplitude errors)

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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
