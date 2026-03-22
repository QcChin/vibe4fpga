"""Quartus Prime Tcl batch runner.

Executes compilation flows via quartus_sh --script (batch mode).
Supports:
  - Full compilation (Analysis + Synthesis + Fit + Assemble + Timing)
  - Analysis & Synthesis only
  - TimeQuest timing analysis
  - Programmer (SVF/JTAG/USB-Blaster)

Design doc reference: Phase 4 — Quartus深度集成
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path


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


# ── Runner ────────────────────────────────────────────────────────────────────

async def _run_quartus(
    quartus_sh: str,
    tcl_script: str,
    cwd: str,
    timeout: int = 600,
) -> tuple[str, str, int]:
    """Execute quartus_sh with a Tcl script. Returns (stdout, stderr, returncode)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tcl", delete=False, dir=cwd
    ) as f:
        f.write(tcl_script)
        tcl_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            quartus_sh, "--script", tcl_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout_b.decode(), stderr_b.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        return "", f"Quartus timed out after {timeout}s", -1
    except FileNotFoundError:
        return "", f"quartus_sh not found at '{quartus_sh}'. Check QUARTUS_ROOTDIR.", -1
    finally:
        Path(tcl_path).unlink(missing_ok=True)


async def compile_project(
    project_dir: str,
    project_name: str,
    revision: str = "",
    quartus_sh: str = "quartus_sh",
    timeout: int = 600,
) -> dict:
    """Full Quartus compilation (Analysis→Synthesis→Fit→Assemble→Timing)."""
    rev = revision or project_name
    tcl = _COMPILE_TCL.format(revision=rev, project_name=project_name)
    stdout, stderr, rc = await _run_quartus(quartus_sh, tcl, project_dir, timeout)
    result = _parse_compilation_output(stdout, stderr)
    result["returncode"] = rc
    result["log_excerpt"] = (stdout + stderr)[-3000:]   # last 3 KB
    return result


async def synthesize_only(
    project_dir: str,
    project_name: str,
    revision: str = "",
    quartus_sh: str = "quartus_sh",
    timeout: int = 300,
) -> dict:
    """Analysis & Synthesis only (fast, no place-and-route)."""
    rev = revision or project_name
    tcl = _SYNTH_ONLY_TCL.format(revision=rev, project_name=project_name)
    stdout, stderr, rc = await _run_quartus(quartus_sh, tcl, project_dir, timeout)
    result = _parse_compilation_output(stdout, stderr)
    result["returncode"] = rc
    result["log_excerpt"] = (stdout + stderr)[-3000:]
    return result


async def run_timing_analysis(
    project_dir: str,
    project_name: str,
    revision: str = "",
    quartus_sh: str = "quartus_sh",
    timeout: int = 120,
) -> dict:
    """TimeQuest Timing Analysis — returns Fmax, WNS, per-clock slack."""
    rev = revision or project_name
    report_file = str(Path(project_dir) / "timing_summary.txt")
    fmax_file   = str(Path(project_dir) / "fmax_summary.txt")
    tcl = _TIMING_TCL.format(
        revision=rev,
        project_name=project_name,
        report_file=report_file,
        fmax_file=fmax_file,
    )
    stdout, stderr, rc = await _run_quartus(quartus_sh, tcl, project_dir, timeout)
    result = _parse_compilation_output(stdout, stderr)
    result["returncode"] = rc

    # Read exported report files if they exist
    for key, fpath in [("timing_report", report_file), ("fmax_report", fmax_file)]:
        p = Path(fpath)
        if p.exists():
            result[key] = p.read_text(errors="replace")[:4000]

    return result


async def program_device(
    sof_file: str,
    quartus_sh: str = "quartus_sh",
    timeout: int = 60,
) -> dict:
    """Download .sof bitfile to device via USB-Blaster JTAG."""
    tcl = _PROGRAM_TCL.format(sof_file=sof_file)
    sof_dir = str(Path(sof_file).parent)
    stdout, stderr, rc = await _run_quartus(quartus_sh, tcl, sof_dir, timeout)
    return {
        "success": rc == 0 and "Error" not in stderr,
        "returncode": rc,
        "output": (stdout + stderr)[-2000:],
    }
