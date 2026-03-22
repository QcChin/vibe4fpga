"""Generic CSV waveform reader — universal fallback for any oscilloscope.

Handles most common CSV export formats:
  - Two-column: time, voltage
  - Multi-column with header row(s)
  - Comma or tab separated
  - Time axis in seconds, milliseconds, microseconds, or nanoseconds
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path


_TIME_UNIT_TO_NS = {
    "s":  1e9,
    "ms": 1e6,
    "us": 1e3,
    "µs": 1e3,
    "ns": 1.0,
    "ps": 1e-3,
}


def _detect_time_unit(header_text: str, first_time_value: float) -> float:
    """Infer the time-to-nanoseconds multiplier from header text or value magnitude.

    If a unit string is found in the header, use it.
    Otherwise, infer from the order of magnitude of the first time value:
      < 1e-6  → probably seconds (multiply by 1e9)
      < 1e-3  → probably milliseconds (multiply by 1e6)
      < 1     → probably microseconds (multiply by 1e3)
      ≥ 1     → probably nanoseconds (multiply by 1)
    """
    header_lower = header_text.lower()
    for unit, factor in _TIME_UNIT_TO_NS.items():
        if unit in header_lower:
            return factor

    if first_time_value == 0:
        return 1e9  # default to seconds

    abs_val = abs(first_time_value)
    if abs_val < 1e-6:
        return 1e9   # seconds
    if abs_val < 1e-3:
        return 1e6   # milliseconds
    if abs_val < 1.0:
        return 1e3   # microseconds
    return 1.0       # nanoseconds


def _parse_float(s: str) -> float | None:
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def read_csv_auto(file_path: str, channel: int = 0) -> dict:
    """Parse any oscilloscope CSV export.

    Args:
        file_path: Path to CSV file.
        channel:   0-based channel index when multiple voltage columns are present.

    Returns:
        {
            "time_ns":        list[float],
            "voltage_v":      list[float],
            "sample_rate_hz": float,
            "duration_ns":    float,
            "channel":        int,
            "vendor":         "generic",
        }
    """
    fp = Path(file_path)
    raw = fp.read_text(errors="replace")

    # Auto-detect delimiter
    delimiter = "\t" if raw.count("\t") > raw.count(",") else ","

    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        return {"error": "Empty CSV file"}

    # Skip header rows (rows where first column can't be parsed as float)
    header_text = ""
    data_start = 0
    for i, row in enumerate(rows):
        if row and _parse_float(row[0]) is not None:
            data_start = i
            break
        header_text += " ".join(row) + " "

    data_rows = rows[data_start:]
    if not data_rows:
        return {"error": "No numeric data found in CSV"}

    # Collect time and voltage columns
    times_raw:    list[float] = []
    voltages_raw: list[float] = []

    # Determine which column is voltage
    # If 2+ columns: col 0 = time, col 1+channel = voltage
    volt_col = min(1 + channel, (len(data_rows[0]) - 1)) if len(data_rows[0]) > 1 else 0

    for row in data_rows:
        if not row:
            continue
        t = _parse_float(row[0])
        v = _parse_float(row[volt_col]) if len(row) > volt_col else None
        if t is not None and v is not None:
            times_raw.append(t)
            voltages_raw.append(v)

    if not times_raw:
        return {"error": "Could not extract numeric time/voltage columns"}

    # Convert time to ns
    ts_factor = _detect_time_unit(header_text, times_raw[0])
    time_ns = [t * ts_factor for t in times_raw]

    duration_ns = time_ns[-1] - time_ns[0] if len(time_ns) > 1 else 0.0
    sample_rate_hz = (
        (len(time_ns) - 1) / (duration_ns * 1e-9)
        if duration_ns > 0
        else 0.0
    )

    return {
        "time_ns":        time_ns,
        "voltage_v":      voltages_raw,
        "sample_rate_hz": round(sample_rate_hz),
        "duration_ns":    round(duration_ns, 3),
        "channel":        channel,
        "vendor":         "generic",
        "points":         len(time_ns),
    }
