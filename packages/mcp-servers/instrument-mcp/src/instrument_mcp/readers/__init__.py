"""Oscilloscope / spectrum analyzer file readers.

Supported formats (Phase 3):
  Rigol DS1054Z   — CSV direct export (most common entry-level oscilloscope)
  Tektronix       — CSV via SCPI SAVe:WAVEform (WFM binary deferred to Phase 4)
  Keysight        — .csv / .bin (binary via struct parsing)
  SIGLENT         — .xml + .csv
  Generic         — auto-detect 2-column CSV (universal fallback)
"""

from .generic import read_csv_auto
from .rigol import read_rigol_csv

__all__ = ["read_csv_auto", "read_rigol_csv"]
