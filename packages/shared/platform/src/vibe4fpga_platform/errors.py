"""Exceptions raised by vibe4fpga-platform helpers."""

from __future__ import annotations


class PlatformError(Exception):
    """Base exception for platform helpers."""


class ToolNotFoundError(PlatformError):
    """Requested external tool (Vivado / Quartus / Yosys / ...) is not on PATH."""

    def __init__(self, name: str, searched: list[str] | None = None) -> None:
        self.name     = name
        self.searched = searched or []
        where = f" (searched: {', '.join(self.searched)})" if self.searched else ""
        super().__init__(f"Tool '{name}' not found on PATH{where}")


class ProcessTimeoutError(PlatformError):
    """Subprocess exceeded the caller-supplied timeout."""

    def __init__(self, cmd: list[str], timeout: float) -> None:
        self.cmd     = cmd
        self.timeout = timeout
        super().__init__(f"Command timed out after {timeout}s: {' '.join(cmd)}")
