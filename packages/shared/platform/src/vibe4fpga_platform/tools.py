"""External tool discovery.

Wraps :func:`shutil.which` with:

* an optional env-var override (e.g. ``QUARTUS_SH`` → absolute path)
* extra search paths (vendor install dirs like ``C:\\Xilinx\\Vivado\\2024.2\\bin``)
* explicit ``.exe`` fallback on Windows when callers passed a bare name
  (``shutil.which`` already consults PATHEXT, but we belt-and-brace against
  hosts that clear it)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .errors import ToolNotFoundError
from .os_id import IS_WINDOWS


def _compose_path(extra_paths: list[Path] | None) -> str:
    path_env = os.environ.get("PATH", "")
    if not extra_paths:
        return path_env
    extras = os.pathsep.join(str(Path(p)) for p in extra_paths)
    # Extras win over system PATH so vendor hints take precedence.
    return extras + os.pathsep + path_env if path_env else extras


def find_tool(
    name: str,
    *,
    windows_exe: bool = True,
    extra_paths: list[Path] | None = None,
    env_var: str | None = None,
) -> Path | None:
    """Return the resolved path to an external tool, or ``None`` if absent.

    Resolution order:
        1. ``env_var`` override — if set and points to an existing file, wins.
        2. ``shutil.which(name, path=<PATH + extras>)`` — honours PATHEXT on Windows.
        3. On Windows, retry with ``.exe`` suffix in case PATHEXT is missing.

    Args:
        name:        binary name (e.g. ``"vivado"``, ``"yosys"``).
        windows_exe: retry with ``.exe`` on Windows when PATHEXT lookup misses.
        extra_paths: additional directories prepended to PATH (vendor install dirs).
        env_var:     env var that may contain an absolute path override.
    """
    # 1. Explicit env-var override.
    if env_var:
        override = os.environ.get(env_var, "").strip()
        if override:
            p = Path(override)
            if p.is_file():
                return p

    composed = _compose_path(extra_paths)

    # 2. PATH + extras lookup (shutil.which handles PATHEXT on Windows).
    found = shutil.which(name, path=composed)
    if found:
        return Path(found)

    # 3. Belt-and-brace: on Windows, force the ``.exe`` suffix.
    if windows_exe and IS_WINDOWS and not name.lower().endswith((".exe", ".bat", ".cmd")):
        found = shutil.which(f"{name}.exe", path=composed)
        if found:
            return Path(found)

    return None


def require_tool(
    name: str,
    *,
    windows_exe: bool = True,
    extra_paths: list[Path] | None = None,
    env_var: str | None = None,
) -> Path:
    """Like :func:`find_tool` but raises :class:`ToolNotFoundError` on miss."""
    resolved = find_tool(
        name,
        windows_exe=windows_exe,
        extra_paths=extra_paths,
        env_var=env_var,
    )
    if resolved is None:
        searched = [str(p) for p in (extra_paths or [])]
        if env_var:
            searched.append(f"${env_var}")
        raise ToolNotFoundError(name, searched=searched)
    return resolved
