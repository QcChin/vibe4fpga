"""Time-domain alignment of simulation waveform vs. real measurement.

Cross-correlation alignment algorithm (from design doc):
  1. Resample both signals to the same time axis
  2. Compute cross-correlation: xcorr(sim, meas) over all time shifts
  3. Optimal offset: Δt = argmax(xcorr)
  4. Shift real measurement by Δt
  5. Compute point-by-point difference: diff[i] = meas_aligned[i] - sim[i]

The simulation starts from t=0; oscilloscopes use the trigger point as origin.
Direct comparison without alignment is meaningless.
"""

from __future__ import annotations

import math


def _require_numpy():
    try:
        import numpy as np
        return np
    except ImportError:
        raise RuntimeError("numpy not installed: pip install numpy")


def _require_scipy():
    try:
        from scipy import signal as sp_signal
        return sp_signal
    except ImportError:
        raise RuntimeError("scipy not installed: pip install scipy")


def resample_to_grid(
    time_ns: list[float],
    voltage_v: list[float],
    target_time_ns: list[float],
) -> list[float]:
    """Linear interpolation of (time, voltage) onto a target time grid.

    Any target time outside the source range is extrapolated as the boundary value.
    """
    np = _require_numpy()
    t_src  = np.array(time_ns)
    v_src  = np.array(voltage_v)
    t_tgt  = np.array(target_time_ns)
    return list(np.interp(t_tgt, t_src, v_src))


def cross_correlate_align(
    sim_time_ns:   list[float],
    sim_voltage:   list[float],
    meas_time_ns:  list[float],
    meas_voltage:  list[float],
    max_shift_ns:  float | None = None,
) -> dict:
    """Find optimal time alignment via cross-correlation.

    Algorithm:
      Δt = argmax(xcorr(meas, sim))   [in samples, then converted to ns]

    Args:
        sim_time_ns:   Simulation time axis (ns).
        sim_voltage:   Simulation voltage/logic values.
        meas_time_ns:  Measurement time axis (ns).
        meas_voltage:  Measurement voltage values.
        max_shift_ns:  Maximum allowed shift; None = no limit.

    Returns:
        {
            "offset_ns":       float,   # Δt: meas is this many ns ahead of sim
            "offset_samples":  int,     # offset in unified sample grid
            "correlation_peak": float,  # normalized peak correlation value (0-1)
            "aligned_meas_v":  list[float],  # meas resampled & shifted to sim grid
            "sim_v_aligned":   list[float],  # sim resampled to unified grid
            "time_ns":         list[float],  # unified time axis
            "diff_v":          list[float],  # meas_aligned - sim (point-by-point)
        }
    """
    np        = _require_numpy()
    sp_signal = _require_scipy()

    # Build a unified time grid (use sim's time axis as reference)
    t_unified = sim_time_ns

    sim_v  = np.array(resample_to_grid(sim_time_ns, sim_voltage, t_unified))
    meas_v = np.array(resample_to_grid(meas_time_ns, meas_voltage, t_unified))

    # Cross-correlate
    correlation = sp_signal.correlate(meas_v, sim_v, mode="full")
    lags        = sp_signal.correlation_lags(len(meas_v), len(sim_v), mode="full")

    # Apply max_shift constraint
    if max_shift_ns is not None and len(t_unified) > 1:
        dt_per_sample = (t_unified[-1] - t_unified[0]) / (len(t_unified) - 1)
        max_lag = int(max_shift_ns / dt_per_sample)
        mask = np.abs(lags) <= max_lag
        filtered_corr = np.where(mask, correlation, -np.inf)
        peak_idx = int(np.argmax(filtered_corr))
    else:
        peak_idx = int(np.argmax(correlation))

    offset_samples = int(lags[peak_idx])

    # Convert offset to nanoseconds
    dt = (t_unified[-1] - t_unified[0]) / max(len(t_unified) - 1, 1)
    offset_ns = float(offset_samples * dt)

    # Normalized correlation (0 = no correlation, 1 = perfect)
    norm = float(
        correlation[peak_idx]
        / max(np.sqrt(np.sum(sim_v ** 2) * np.sum(meas_v ** 2)), 1e-12)
    )

    # Shift meas by -offset_samples to align with sim
    if offset_samples > 0:
        aligned = np.concatenate([np.zeros(offset_samples), meas_v[:-offset_samples or None]])
    elif offset_samples < 0:
        pad = -offset_samples
        aligned = np.concatenate([meas_v[pad:], np.zeros(pad)])
    else:
        aligned = meas_v.copy()

    diff = (aligned - sim_v).tolist()

    return {
        "offset_ns":        round(offset_ns, 3),
        "offset_samples":   offset_samples,
        "correlation_peak": round(norm, 4),
        "aligned_meas_v":   aligned.tolist(),
        "sim_v_aligned":    sim_v.tolist(),
        "time_ns":          list(t_unified),
        "diff_v":           diff,
    }


def compute_fft(
    time_ns: list[float],
    voltage_v: list[float],
    n_points: int = 1024,
) -> dict:
    """Compute FFT of a waveform for spectral analysis.

    Returns:
        {
            "freq_mhz":   list[float],   # frequency axis
            "amplitude":  list[float],   # magnitude spectrum (dBV or linear)
            "dominant_freq_mhz": float,  # peak frequency
            "thd_pct":    float | None,  # total harmonic distortion estimate
        }
    """
    np = _require_numpy()

    t  = np.array(time_ns)
    v  = np.array(voltage_v)

    if len(t) < 2:
        return {"error": "Insufficient data points for FFT"}

    # Compute sample rate
    dt_s = (t[-1] - t[0]) * 1e-9 / (len(t) - 1)
    if dt_s <= 0:
        return {"error": "Invalid time axis for FFT"}

    fs = 1.0 / dt_s   # samples per second

    # Windowed FFT
    window = np.hanning(len(v))
    spectrum = np.abs(np.fft.rfft(v * window, n=n_points))
    freqs_hz = np.fft.rfftfreq(n_points, d=dt_s)

    # Normalize
    spectrum = spectrum / (n_points / 2)

    freq_mhz  = (freqs_hz / 1e6).tolist()
    amplitude = spectrum.tolist()

    # Find dominant frequency
    peak_idx = int(np.argmax(spectrum[1:])) + 1   # skip DC
    dominant_freq_mhz = float(freqs_hz[peak_idx] / 1e6)

    # Crude THD estimate (ratio of harmonics to fundamental)
    f0_idx = peak_idx
    harmonic_power = 0.0
    for h in range(2, 6):
        h_idx = f0_idx * h
        if h_idx < len(spectrum):
            harmonic_power += spectrum[h_idx] ** 2
    fundamental_power = spectrum[f0_idx] ** 2
    thd_pct = (
        math.sqrt(harmonic_power / max(fundamental_power, 1e-30)) * 100.0
        if fundamental_power > 0
        else None
    )

    return {
        "freq_mhz":          freq_mhz[:256],   # cap output size
        "amplitude":         amplitude[:256],
        "dominant_freq_mhz": round(dominant_freq_mhz, 4),
        "thd_pct":           round(thd_pct, 2) if thd_pct is not None else None,
        "sample_rate_mhz":   round(fs / 1e6, 3),
    }
