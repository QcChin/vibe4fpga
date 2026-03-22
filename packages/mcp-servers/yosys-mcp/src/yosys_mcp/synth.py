"""Yosys synthesis wrapper.

Supports:
  - Generic synthesis (synth)
  - iCE40 target (synth_ice40 → iCE40LP/HX/UP)
  - ECP5 target   (synth_ecp5 → Lattice ECP5)
  - Generic ASIC  (synth → techmap with liberty file)

Design doc reference: Phase 4 — Yosys/nextpnr开源工具链支持
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path


# ── Script templates ──────────────────────────────────────────────────────────

_SYNTH_ICE40 = """\
# Yosys iCE40 synthesis
read_verilog {read_flags} {source_files}
synth_ice40 -top {top_module} -json {output_json} {extra_flags}
"""

_SYNTH_ECP5 = """\
# Yosys ECP5 synthesis
read_verilog {read_flags} {source_files}
synth_ecp5 -top {top_module} -json {output_json} {extra_flags}
"""

_SYNTH_GENERIC = """\
# Yosys generic synthesis
read_verilog {read_flags} {source_files}
synth -top {top_module}
write_json {output_json}
"""

_FORMAL_PREP = """\
# Yosys formal verification preparation
read_verilog -sv {source_files}
prep -top {top_module}
write_smt2 -wires {output_smt2}
"""

# Targets mapping → script template
_SYNTH_SCRIPTS = {
    "ice40":   _SYNTH_ICE40,
    "ecp5":    _SYNTH_ECP5,
    "generic": _SYNTH_GENERIC,
}


# ── Parsing ───────────────────────────────────────────────────────────────────

_ERROR_RE   = re.compile(r"^ERROR:\s+(.+)", re.MULTILINE)
_WARNING_RE = re.compile(r"^WARNING:\s+(.+)", re.MULTILINE)
_STAT_CELL_RE = re.compile(r"\$(\w+)\s+(\d+)")
_WIRE_RE    = re.compile(r"Number of wires:\s+(\d+)")
_CELL_RE    = re.compile(r"Number of cells:\s+(\d+)")
_LUT_RE     = re.compile(r"(?:SB_LUT4|LUT4)\s+(\d+)")
_FF_RE      = re.compile(r"(?:SB_DFF\w*|FD\w*)\s+(\d+)")
_BRAM_RE    = re.compile(r"(?:SB_RAM\w*|DP16KD)\s+(\d+)")


def _parse_yosys_output(stdout: str, stderr: str) -> dict:
    text = stdout + "\n" + stderr
    errors   = [m.group(1).strip() for m in _ERROR_RE.finditer(text)]
    warnings = [m.group(1).strip() for m in _WARNING_RE.finditer(text)]

    # Cell statistics
    lut_m   = _LUT_RE.search(text)
    ff_m    = _FF_RE.search(text)
    bram_m  = _BRAM_RE.search(text)
    wire_m  = _WIRE_RE.search(text)
    cell_m  = _CELL_RE.search(text)

    return {
        "success":  len(errors) == 0,
        "errors":   errors,
        "warnings": warnings[:20],    # cap to avoid context explosion
        "cells": {
            "lut":   int(lut_m.group(1))  if lut_m  else None,
            "ff":    int(ff_m.group(1))   if ff_m   else None,
            "bram":  int(bram_m.group(1)) if bram_m else None,
            "wires": int(wire_m.group(1)) if wire_m else None,
            "total": int(cell_m.group(1)) if cell_m else None,
        },
    }


# ── Yosys runner ──────────────────────────────────────────────────────────────

async def _run_yosys(script: str, work_dir: str, timeout: int = 120) -> tuple[str, str, int]:
    """Execute yosys with an inline script. Returns (stdout, stderr, returncode)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ys", delete=False, dir=work_dir
    ) as f:
        f.write(script)
        script_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "yosys", "-s", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout_b.decode(), stderr_b.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        return "", f"Yosys timed out after {timeout}s", -1
    except FileNotFoundError:
        return "", "yosys not found. Install: sudo apt install yosys", -1
    finally:
        Path(script_path).unlink(missing_ok=True)


async def synthesize(
    source_files: list[str],
    top_module: str,
    target: str = "ice40",
    work_dir: str | None = None,
    sv_mode: bool = False,
    extra_flags: str = "",
    timeout: int = 120,
) -> dict:
    """Run Yosys synthesis.

    Args:
        source_files: List of Verilog/SystemVerilog source paths.
        top_module:   Top-level module name.
        target:       Synthesis target: "ice40" | "ecp5" | "generic".
        work_dir:     Working directory for output files (temp dir if None).
        sv_mode:      Pass -sv flag to read_verilog for SystemVerilog.
        extra_flags:  Additional flags for synth_* command.
        timeout:      Synthesis timeout in seconds.

    Returns:
        {success, errors, warnings, cells, output_json}
    """
    import tempfile as _tempfile

    wd = work_dir or _tempfile.mkdtemp()
    output_json = str(Path(wd) / f"{top_module}_synth.json")
    read_flags = "-sv" if sv_mode else ""
    sources = " ".join(f'"{f}"' for f in source_files)

    template = _SYNTH_SCRIPTS.get(target, _SYNTH_GENERIC)
    script = template.format(
        read_flags=read_flags,
        source_files=sources,
        top_module=top_module,
        output_json=output_json,
        extra_flags=extra_flags,
    )

    stdout, stderr, rc = await _run_yosys(script, wd, timeout)
    result = _parse_yosys_output(stdout, stderr)
    result["returncode"] = rc
    result["output_json"] = output_json if Path(output_json).exists() else None
    result["log_excerpt"] = (stdout + stderr)[-2000:]
    return result


async def prepare_formal(
    source_files: list[str],
    top_module: str,
    work_dir: str | None = None,
    timeout: int = 60,
) -> dict:
    """Prepare SMT2 file for formal verification (SymbiYosys input).

    Returns:
        {success, errors, output_smt2}
    """
    import tempfile as _tempfile

    wd = work_dir or _tempfile.mkdtemp()
    output_smt2 = str(Path(wd) / f"{top_module}.smt2")
    sources = " ".join(f'"{f}"' for f in source_files)

    script = _FORMAL_PREP.format(
        source_files=sources,
        top_module=top_module,
        output_smt2=output_smt2,
    )

    stdout, stderr, rc = await _run_yosys(script, wd, timeout)
    result = _parse_yosys_output(stdout, stderr)
    result["returncode"] = rc
    result["output_smt2"] = output_smt2 if Path(output_smt2).exists() else None
    return result
