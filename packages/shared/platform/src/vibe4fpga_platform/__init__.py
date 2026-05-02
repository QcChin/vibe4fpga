"""vibe4fpga-platform — cross-platform helpers for MCP servers.

Centralises scratch-path, tool-discovery, and subprocess handling so every
MCP server in the vibe4fpga monorepo behaves identically on macOS and Windows.

Public API:

    from vibe4fpga_platform import (
        IS_WINDOWS, IS_MAC, IS_LINUX,
        scratch_dir, scratch_file,
        find_tool, require_tool,
        run, Completed,
        ToolNotFoundError, ProcessTimeoutError, PlatformError,
    )
"""

from __future__ import annotations

from .errors import PlatformError, ProcessTimeoutError, ToolNotFoundError
from .os_id import IS_LINUX, IS_MAC, IS_WINDOWS
from .paths import long_path, normalize_for_subprocess, scratch_dir, scratch_file
from .proc import Completed, run
from .tools import find_tool, require_tool

__all__ = [
    "IS_WINDOWS",
    "IS_MAC",
    "IS_LINUX",
    "scratch_dir",
    "scratch_file",
    "long_path",
    "normalize_for_subprocess",
    "find_tool",
    "require_tool",
    "run",
    "Completed",
    "PlatformError",
    "ProcessTimeoutError",
    "ToolNotFoundError",
]

__version__ = "0.1.0"
