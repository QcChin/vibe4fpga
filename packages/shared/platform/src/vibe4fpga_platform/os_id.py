"""Platform detection constants.

Single source of truth — all other modules in this package and downstream MCPs
should read these constants rather than re-computing ``sys.platform`` checks.
"""

from __future__ import annotations

import sys

IS_WINDOWS: bool = sys.platform == "win32"
IS_MAC:     bool = sys.platform == "darwin"
IS_LINUX:   bool = sys.platform.startswith("linux")
