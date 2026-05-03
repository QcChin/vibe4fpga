"""Cross-platform scratch-directory and temp-file helpers.

Replaces ``/tmp/...`` hardcoded paths (which break on Windows) with properly
registered temp roots that are cleaned on process exit. A single per-process
scratch root is reused across ``scratch_file()`` calls to avoid cluttering
the system tempdir with dozens of `vibe4fpga_*` directories.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .os_id import IS_WINDOWS

# Registered scratch roots — cleaned on interpreter shutdown.
_registered_roots: list[Path] = []


def _cleanup_roots() -> None:
    for root in _registered_roots:
        shutil.rmtree(root, ignore_errors=True)


atexit.register(_cleanup_roots)


def scratch_dir(prefix: str = "vibe4fpga_") -> Path:
    """Create a fresh scratch directory that will be removed on process exit.

    Args:
        prefix: directory-name prefix. Default ``vibe4fpga_``.

    Returns:
        Absolute :class:`Path` to a newly created directory.
    """
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _registered_roots.append(d)
    return d


# Lazy singleton — created on first `scratch_file()` call.
_shared_root: Path | None = None


def _shared_scratch_root() -> Path:
    global _shared_root
    if _shared_root is None:
        _shared_root = scratch_dir("vibe4fpga_files_")
    return _shared_root


def scratch_file(suffix: str = "") -> Path:
    """Return an unused path inside the per-process scratch directory.

    Does NOT create the file — callers open it themselves. The parent
    directory is guaranteed to exist and is auto-cleaned on process exit.

    Args:
        suffix: filename suffix, including leading dot (e.g. ``".vvp"``).
    """
    stem = uuid.uuid4().hex[:12]
    return _shared_scratch_root() / f"{stem}{suffix}"


def long_path(p: Path | str) -> Path:
    """Return ``p`` with the Windows ``\\\\?\\`` long-path prefix when needed.

    No-op on non-Windows platforms and for already-prefixed paths.
    Applied when the absolute path length would otherwise exceed the 240-char
    soft limit (260 hard limit minus headroom for filename expansion).

    Uses :func:`os.path.abspath` rather than :meth:`Path.resolve` so the
    function works on paths that don't exist yet (a common case for
    subprocess output files whose path we need to stringify before the
    child process creates them). ``resolve()`` raises on missing targets on
    older Windows Pythons and performs symlink resolution we don't want.
    """
    p_str = os.fspath(p)
    # Already prefixed — pass through unchanged regardless of OS.
    if p_str.startswith("\\\\?\\"):
        return Path(p_str)
    if not IS_WINDOWS:
        return Path(p_str)
    absolute = os.path.abspath(p_str)   # filesystem-free; works on non-existent paths
    if len(absolute) <= 240:
        return Path(absolute)
    return Path("\\\\?\\" + absolute)


def normalize_for_subprocess(p: Path | str | None) -> str | None:
    """Stringify a path for passing to subprocess, applying long-path prefix on Windows.

    Returns ``None`` if ``p`` is ``None``.
    """
    if p is None:
        return None
    return str(long_path(p)) if IS_WINDOWS else os.fspath(p)
