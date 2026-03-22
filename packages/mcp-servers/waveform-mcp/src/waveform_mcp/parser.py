"""VCD/FST waveform parser with timescale normalization.

Uses vcdvcd for VCD parsing. FST is handled via GTKWave's fst2vcd conversion
when available, falling back to a "not supported" message.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ── Timescale utilities ───────────────────────────────────────────────────────

_TS_UNITS = {"fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}


def timescale_to_ns(ts: str) -> float:
    """Parse a VCD timescale string and return the ns multiplier.

    e.g. "1ns" → 1.0,  "10ps" → 0.01,  "1us" → 1000.0
    """
    ts = ts.strip().lower()
    for unit, factor in _TS_UNITS.items():
        if ts.endswith(unit):
            try:
                value = float(ts[: -len(unit)].strip() or "1")
                return value * factor
            except ValueError:
                return factor
    return 1.0  # default: assume ns


# ── Signal data model ─────────────────────────────────────────────────────────

@dataclass
class SignalTrace:
    name: str
    width: int                              # bit width
    scope: str                              # HDL scope path
    tv: list[tuple[float, str]] = field(default_factory=list)   # (time_ns, value_str)

    @property
    def is_clock(self) -> bool:
        """Heuristic: regular binary toggle with ≥20 transitions."""
        if self.width != 1 or len(self.tv) < 20:
            return False
        vals = [v for _, v in self.tv[:40]]
        # Must alternate 0/1
        alternating = all(
            vals[i] != vals[i + 1] for i in range(min(10, len(vals) - 1))
        )
        if not alternating:
            return False
        # Period must be consistent (std < 5% of mean)
        periods = [self.tv[i + 1][0] - self.tv[i][0] for i in range(min(20, len(self.tv) - 1))]
        if not periods:
            return False
        mean = sum(periods) / len(periods)
        if mean <= 0:
            return False
        std = (sum((p - mean) ** 2 for p in periods) / len(periods)) ** 0.5
        return (std / mean) < 0.05

    @property
    def clock_period_ns(self) -> float:
        """Full clock period in ns (2 × half-period)."""
        if len(self.tv) < 2:
            return 0.0
        half_periods = [
            self.tv[i + 1][0] - self.tv[i][0]
            for i in range(min(10, len(self.tv) - 1))
        ]
        return 2.0 * (sum(half_periods) / len(half_periods)) if half_periods else 0.0


@dataclass
class WaveformMetadata:
    format: str                             # "vcd" | "fst"
    file_path: str
    duration_ns: float
    ts_factor_ns: float                     # timescale factor in ns
    signals: dict[str, SignalTrace] = field(default_factory=dict)

    @property
    def clocks(self) -> list[dict]:
        result = []
        for name, sig in self.signals.items():
            if sig.is_clock:
                period = sig.clock_period_ns
                result.append({
                    "name":      name,
                    "period_ns": round(period, 3),
                    "freq_mhz":  round(1000.0 / period, 3) if period > 0 else 0,
                })
        return result

    def to_summary(self) -> dict:
        return {
            "format":       self.format,
            "duration_ns":  round(self.duration_ns, 3),
            "signal_count": len(self.signals),
            "clocks":       self.clocks,
            "signals": [
                {
                    "name":  name,
                    "width": sig.width,
                    "scope": sig.scope,
                }
                for name, sig in list(self.signals.items())[:200]   # cap for large projects
            ],
        }


# ── VCD parser ────────────────────────────────────────────────────────────────

def parse_vcd(file_path: str) -> WaveformMetadata:
    """Parse a VCD file and return WaveformMetadata with all signal traces.

    Uses vcdvcd library for robust VCD parsing.
    """
    try:
        from vcdvcd import VCDVCD
    except ImportError as e:
        raise RuntimeError("vcdvcd not installed: pip install vcdvcd") from e

    vcd = VCDVCD(file_path, only_sigs=False, store_tvs=True)

    ts_str = getattr(vcd, "timescale", "1ns")
    if isinstance(ts_str, dict):
        # Some versions return {"timescale": "1ns"}
        ts_str = ts_str.get("timescale", "1ns")
    ts_factor = timescale_to_ns(str(ts_str))

    endtime_raw = getattr(vcd, "endtime", 0) or 0
    duration_ns = float(endtime_raw) * ts_factor

    signals: dict[str, SignalTrace] = {}
    for sig_name in vcd.signals:
        try:
            data = vcd[sig_name]
            scope_parts = sig_name.rsplit(".", 1)
            scope = scope_parts[0] if len(scope_parts) > 1 else ""
            tv_ns = [(float(t) * ts_factor, v) for t, v in (data.tv or [])]
            signals[sig_name] = SignalTrace(
                name=sig_name,
                width=int(getattr(data, "size", 1)),
                scope=scope,
                tv=tv_ns,
            )
        except Exception:
            continue  # skip signals that fail to parse

    return WaveformMetadata(
        format="vcd",
        file_path=file_path,
        duration_ns=duration_ns,
        ts_factor_ns=ts_factor,
        signals=signals,
    )


def parse_fst(file_path: str) -> WaveformMetadata:
    """Parse an FST file by converting to VCD via GTKWave's fst2vcd tool."""
    if not shutil.which("fst2vcd"):
        raise RuntimeError(
            "fst2vcd not found. Install GTKWave (brew install gtkwave) to enable FST parsing."
        )
    with tempfile.NamedTemporaryFile(suffix=".vcd", delete=False) as tmp:
        tmp_path = tmp.name

    subprocess.run(
        ["fst2vcd", "-o", tmp_path, file_path],
        check=True,
        capture_output=True,
    )
    meta = parse_vcd(tmp_path)
    meta.format = "fst"
    meta.file_path = file_path
    Path(tmp_path).unlink(missing_ok=True)
    return meta


def parse_waveform(file_path: str) -> WaveformMetadata:
    """Auto-detect format and parse waveform file."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".vcd":
        return parse_vcd(file_path)
    if suffix == ".fst":
        return parse_fst(file_path)
    raise ValueError(f"Unsupported waveform format: {suffix}. Supported: .vcd, .fst")
