"""Rigol DS1054Z (and DS/MSO series) CSV waveform reader.

Rigol CSV export format (Oscilloscope → Storage → CSV):
  Lines starting with '#' are metadata:
    #Model,DS1054Z
    #Channel,CH1
    #SampleRate,1.00GSa/s
    #Memory depth,12000 pts
    #Time,2024-01-01 12:00:00
  Followed by two-column data: X (seconds), CHn (volts)

Also handles the simpler Rigol format without '#' headers
(just raw X,CH1 data with a single header line).
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .generic import _parse_float


def _parse_sample_rate(rate_str: str) -> float:
    """Parse Rigol sample rate string, e.g. '1.00GSa/s' → 1e9."""
    rate_str = rate_str.strip().upper().replace("SA/S", "").replace("SPS", "").strip()
    multipliers = {"G": 1e9, "M": 1e6, "K": 1e3}
    for suffix, mult in multipliers.items():
        if rate_str.endswith(suffix):
            try:
                return float(rate_str[:-1]) * mult
            except ValueError:
                pass
    try:
        return float(rate_str)
    except ValueError:
        return 0.0


def read_rigol_csv(file_path: str, channel: str = "CH1") -> dict:
    """Parse Rigol DS series oscilloscope CSV export.

    Args:
        file_path: Path to Rigol CSV file.
        channel:   Channel name to extract, e.g. "CH1", "CH2".

    Returns:
        {time_ns, voltage_v, sample_rate_hz, duration_ns, channel, vendor, metadata}
    """
    fp = Path(file_path)
    raw = fp.read_text(errors="replace")

    metadata: dict = {"vendor": "rigol", "channel": channel}
    lines = raw.splitlines()

    # Parse '#' metadata lines
    data_lines: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line[1:].split(",", 1)
            if len(parts) == 2:
                key, val = parts[0].strip().lower(), parts[1].strip()
                if "samplerate" in key or "sample rate" in key:
                    metadata["sample_rate_hz"] = _parse_sample_rate(val)
                elif "model" in key:
                    metadata["model"] = val
                elif "channel" in key:
                    metadata["channel"] = val
        else:
            data_lines.append(line)

    # Determine delimiter
    first_data = data_lines[0] if data_lines else ""
    delimiter = "\t" if first_data.count("\t") > first_data.count(",") else ","

    # Parse data rows
    reader = csv.reader(io.StringIO("\n".join(data_lines)), delimiter=delimiter)
    rows = list(reader)

    # Find column index for the requested channel
    volt_col = 1  # default
    header_row_idx = 0
    if rows and not all(_parse_float(c) is not None for c in rows[0] if c.strip()):
        # First row is a header
        for j, col in enumerate(rows[0]):
            if channel.upper() in col.upper():
                volt_col = j
                break
        header_row_idx = 1

    time_ns: list[float] = []
    voltage_v: list[float] = []

    for row in rows[header_row_idx:]:
        if not row or len(row) < 2:
            continue
        t = _parse_float(row[0])
        v = _parse_float(row[volt_col]) if len(row) > volt_col else None
        if t is not None and v is not None:
            time_ns.append(t * 1e9)   # Rigol time is in seconds
            voltage_v.append(v)

    if not time_ns:
        return {"error": f"No data found for channel {channel}", **metadata}

    duration_ns = time_ns[-1] - time_ns[0] if len(time_ns) > 1 else 0.0
    sample_rate = metadata.get("sample_rate_hz") or (
        (len(time_ns) - 1) / (duration_ns * 1e-9) if duration_ns > 0 else 0.0
    )

    return {
        "time_ns":        time_ns,
        "voltage_v":      voltage_v,
        "sample_rate_hz": round(sample_rate),
        "duration_ns":    round(duration_ns, 3),
        "channel":        channel,
        "vendor":         "rigol",
        "points":         len(time_ns),
        "metadata":       metadata,
    }
