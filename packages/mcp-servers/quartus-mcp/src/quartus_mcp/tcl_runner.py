"""Quartus Prime Tcl batch runner.

Executes compilation flows via quartus_sh --script (batch mode).
Supports:
  - Full compilation (Analysis + Synthesis + Fit + Assemble + Timing)
  - Analysis & Synthesis only
  - TimeQuest timing analysis
  - Programmer (SVF/JTAG/USB-Blaster)

All subprocess calls route through :func:`vibe4fpga_platform.run` so
``PYTHONIOENCODING=utf-8`` + Windows UTF-8 codepage + long-path prefixing
are handled uniformly.

Design doc reference: Phase 4 — Quartus深度集成
"""

from __future__ import annotations

import re
from pathlib import Path

from vibe4fpga_platform import (
    ProcessTimeoutError,
    ToolNotFoundError,
    find_tool,
    require_tool,
    run,
    scratch_file,
)


# ── TCL script templates ──────────────────────────────────────────────────────

_COMPILE_TCL = """\
# Quartus Prime full compilation
package require ::quartus::project
package require ::quartus::flow

project_open -revision {revision} {project_name}
execute_flow -compile
project_close
"""

_SYNTH_ONLY_TCL = """\
# Analysis & Synthesis only
package require ::quartus::project
package require ::quartus::flow

project_open -revision {revision} {project_name}
execute_flow -analysis_and_synthesis
project_close
"""

_TIMING_TCL = """\
# TimeQuest Timing Analysis
package require ::quartus::project
package require ::quartus::sta

project_open -revision {revision} {project_name}
create_timing_netlist
read_sdc
update_timing_netlist

# Export summary to file
report_timing_summary -file {report_file}
report_clock_fmax_summary -file {fmax_file}

delete_timing_netlist
project_close
"""

_PROGRAM_TCL = """\
# JTAG programming via USB-Blaster
package require ::quartus::jtag

foreach hardware_name [get_hardware_names] {{
    if {{[string match "*USB-Blaster*" $hardware_name]}} {{
        foreach device_name [get_device_names -hardware_name $hardware_name] {{
            if {{[string match "@1*" $device_name]}} {{
                device_lock -hardware_name $hardware_name -device_name $device_name
                device_download_sof \\
                    -hardware_name $hardware_name \\
                    -device_name   $device_name   \\
                    -sof_path      {sof_file}
                device_unlock -hardware_name $hardware_name -device_name $device_name
                break
            }}
        }}
        break
    }}
}}
"""


# ── Parsing helpers ───────────────────────────────────────────────────────────

_ERROR_RE   = re.compile(r"Error\s*\((\d+)\):\s*(.+)")
_WARNING_RE = re.compile(r"Warning\s*\((\d+)\):\s*(.+)")
_FMAX_RE    = re.compile(r"Fmax\s*=\s*([\d.]+)\s*MHz.*clock\s+(.+?)(?:\s*$)", re.IGNORECASE)
_SLACK_RE   = re.compile(r"Slack\s*:\s*([-\d.]+)\s*ns")
_LUT_RE     = re.compile(r"Total\s+logic\s+elements\s*;\s*([\d,]+)\s*/\s*([\d,]+)")
_REG_RE     = re.compile(r"Total\s+registers\s*;\s*([\d,]+)")
_IO_RE      = re.compile(r"Total\s+pins\s*;\s*([\d,]+)\s*/\s*([\d,]+)")
_MEM_RE     = re.compile(r"Total\s+memory\s+bits\s*;\s*([\d,]+)\s*/\s*([\d,]+)")


def _parse_compilation_output(stdout: str, stderr: str) -> dict:
    text = stdout + "\n" + stderr
    errors   = [{"code": m.group(1), "msg": m.group(2).strip()} for m in _ERROR_RE.finditer(text)]
    warnings = [{"code": m.group(1), "msg": m.group(2).strip()} for m in _WARNING_RE.finditer(text)]

    # Fmax
    fmax: dict[str, float] = {}
    for m in _FMAX_RE.finditer(text):
        fmax[m.group(2).strip()] = float(m.group(1))

    # Slack
    slack_match = _SLACK_RE.search(text)
    worst_slack = float(slack_match.group(1)) if slack_match else None

    # Utilization
    lut_match = _LUT_RE.search(text)
    reg_match = _REG_RE.search(text)
    io_match  = _IO_RE.search(text)
    mem_match = _MEM_RE.search(text)

    def _int(m, g=1): return int(m.group(g).replace(",", "")) if m else None

    return {
        "errors":      errors,
        "warnings":    warnings,
        "fmax_mhz":    fmax,
        "worst_slack_ns": worst_slack,
        "success":     len(errors) == 0,
        "utilization": {
            "lut_used":  _int(lut_match, 1),
            "lut_total": _int(lut_match, 2),
            "reg_used":  _int(reg_match),
            "io_used":   _int(io_match, 1),
            "io_total":  _int(io_match, 2),
            "mem_bits":  _int(mem_match, 1),
        },
    }


# ── Tool discovery + edition detection ────────────────────────────────────────

_EDITION_CACHE: dict[str, str] = {}

_EDITION_RE = re.compile(r"Quartus\s+(?:Prime\s+)?(Pro|Lite|Standard)", re.IGNORECASE)


def resolve_quartus_sh() -> Path:
    """Locate ``quartus_sh``, honoring the ``QUARTUS_SH`` env-var override.

    Raises:
        ToolNotFoundError: if ``quartus_sh`` is not on PATH and ``QUARTUS_SH``
        is unset or points to a non-existent file.
    """
    return require_tool("quartus_sh", env_var="QUARTUS_SH")


def quartus_sh_path() -> Path | None:
    """Non-raising variant for probing availability (used by tests and error
    surfaces). Returns ``None`` if ``quartus_sh`` is unavailable.
    """
    return find_tool("quartus_sh", env_var="QUARTUS_SH")


async def detect_quartus_edition(
    quartus_sh: str | Path | None = None,
    *,
    timeout: float = 30.0,
) -> str:
    """Run ``quartus_sh --version`` and return the edition (``"Pro"`` /
    ``"Lite"`` / ``"Standard"`` / ``"Unknown"``).

    Result is memoized by executable path. Per plan R4 this prevents silent
    feature mismatches between Pro and Lite (e.g. IP availability, SDC
    dialect quirks).
    """
    if quartus_sh is None:
        quartus_sh = resolve_quartus_sh()
    key = str(quartus_sh)
    cached = _EDITION_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        completed = await run([quartus_sh, "--version"], timeout=timeout)
    except ProcessTimeoutError:
        _EDITION_CACHE[key] = "Unknown"
        return "Unknown"

    text = completed.stdout_text() + completed.stderr_text()
    match = _EDITION_RE.search(text)
    edition = match.group(1).capitalize() if match else "Unknown"
    _EDITION_CACHE[key] = edition
    return edition


def _missing_quartus_error(exc: ToolNotFoundError) -> dict:
    """Shape a structured error payload when ``quartus_sh`` is missing."""
    return {
        "success": False,
        "error": "quartus_sh_not_found",
        "message": str(exc),
        "hint": (
            "Install Intel Quartus Prime and either add its ``bin`` directory "
            "to PATH or set QUARTUS_SH / QUARTUS_ROOTDIR."
        ),
    }


# ── Runner ────────────────────────────────────────────────────────────────────

async def _run_quartus(
    quartus_sh: str | Path,
    tcl_script: str,
    cwd: str,
    timeout: int = 600,
) -> tuple[str, str, int]:
    """Execute quartus_sh with a Tcl script. Returns (stdout, stderr, returncode)."""
    tcl_path = scratch_file(".tcl")
    tcl_path.write_text(tcl_script, encoding="utf-8")

    try:
        completed = await run(
            [quartus_sh, "--script", tcl_path],
            cwd=cwd,
            timeout=timeout,
        )
        return (
            completed.stdout_text(),
            completed.stderr_text(),
            completed.returncode,
        )
    except ProcessTimeoutError:
        return "", f"Quartus timed out after {timeout}s", -1
    except FileNotFoundError:
        return "", f"quartus_sh not found at '{quartus_sh}'. Check QUARTUS_ROOTDIR.", -1
    finally:
        tcl_path.unlink(missing_ok=True)


async def compile_project(
    project_dir: str,
    project_name: str,
    revision: str = "",
    quartus_sh: str | Path | None = None,
    timeout: int = 600,
) -> dict:
    """Full Quartus compilation (Analysis→Synthesis→Fit→Assemble→Timing)."""
    try:
        qsh = Path(quartus_sh) if quartus_sh else resolve_quartus_sh()
    except ToolNotFoundError as exc:
        return _missing_quartus_error(exc)
    edition = await detect_quartus_edition(qsh)

    rev = revision or project_name
    tcl = _COMPILE_TCL.format(revision=rev, project_name=project_name)
    stdout, stderr, rc = await _run_quartus(qsh, tcl, project_dir, timeout)
    result = _parse_compilation_output(stdout, stderr)
    result["returncode"]  = rc
    result["edition"]     = edition
    result["log_excerpt"] = (stdout + stderr)[-3000:]   # last 3 KB
    return result


async def synthesize_only(
    project_dir: str,
    project_name: str,
    revision: str = "",
    quartus_sh: str | Path | None = None,
    timeout: int = 300,
) -> dict:
    """Analysis & Synthesis only (fast, no place-and-route)."""
    try:
        qsh = Path(quartus_sh) if quartus_sh else resolve_quartus_sh()
    except ToolNotFoundError as exc:
        return _missing_quartus_error(exc)
    edition = await detect_quartus_edition(qsh)

    rev = revision or project_name
    tcl = _SYNTH_ONLY_TCL.format(revision=rev, project_name=project_name)
    stdout, stderr, rc = await _run_quartus(qsh, tcl, project_dir, timeout)
    result = _parse_compilation_output(stdout, stderr)
    result["returncode"]  = rc
    result["edition"]     = edition
    result["log_excerpt"] = (stdout + stderr)[-3000:]
    return result


async def run_timing_analysis(
    project_dir: str,
    project_name: str,
    revision: str = "",
    quartus_sh: str | Path | None = None,
    timeout: int = 120,
) -> dict:
    """TimeQuest Timing Analysis — returns Fmax, WNS, per-clock slack."""
    try:
        qsh = Path(quartus_sh) if quartus_sh else resolve_quartus_sh()
    except ToolNotFoundError as exc:
        return _missing_quartus_error(exc)
    edition = await detect_quartus_edition(qsh)

    rev = revision or project_name
    report_file = str(Path(project_dir) / "timing_summary.txt")
    fmax_file   = str(Path(project_dir) / "fmax_summary.txt")
    tcl = _TIMING_TCL.format(
        revision=rev,
        project_name=project_name,
        report_file=report_file,
        fmax_file=fmax_file,
    )
    stdout, stderr, rc = await _run_quartus(qsh, tcl, project_dir, timeout)
    result = _parse_compilation_output(stdout, stderr)
    result["returncode"] = rc
    result["edition"]    = edition

    # Read exported report files if they exist
    for key, fpath in [("timing_report", report_file), ("fmax_report", fmax_file)]:
        p = Path(fpath)
        if p.exists():
            result[key] = p.read_text(errors="replace")[:4000]

    return result


async def program_device(
    sof_file: str,
    quartus_sh: str | Path | None = None,
    timeout: int = 60,
) -> dict:
    """Download .sof bitfile to device via USB-Blaster JTAG."""
    try:
        qsh = Path(quartus_sh) if quartus_sh else resolve_quartus_sh()
    except ToolNotFoundError as exc:
        return _missing_quartus_error(exc)
    edition = await detect_quartus_edition(qsh)

    tcl = _PROGRAM_TCL.format(sof_file=sof_file)
    sof_dir = str(Path(sof_file).parent)
    stdout, stderr, rc = await _run_quartus(qsh, tcl, sof_dir, timeout)
    return {
        "success":    rc == 0 and "Error" not in stderr,
        "returncode": rc,
        "edition":    edition,
        "output":     (stdout + stderr)[-2000:],
    }
