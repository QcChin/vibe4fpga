"""Vivado process control — batch mode and persistent TCL session.

Three process communication modes (from design doc):
  Batch mode      (vivado -mode batch)  : synthesis / implementation
  Persistent mode (vivado -mode tcl)    : high-frequency interactive queries
  Journal polling (watchdog file watch) : GUI observer mode, read-only
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path

# TCL template for out-of-context synthesis
_SYNTH_TCL = """\
create_project -in_memory -part {part}
{read_cmds}
synth_design -top {top_module} -part {part} -mode out_of_context
report_timing_summary -no_header -file timing.rpt
report_utilization -no_header -file util.rpt
write_checkpoint -force post_synth.dcp
"""

_TIMING_PARSE_RE = re.compile(
    r"WNS\(ns\)\s+([-\d.]+)\s+TNS\(ns\)\s+([-\d.]+)"
)
_UTIL_RE = re.compile(r"\|\s+([\w/ ]+?)\s+\|\s+(\d+)\s+\|\s+\d+\s+\|\s+(\d+)\s+\|")


def _find_vivado() -> str:
    """Locate Vivado executable; raises RuntimeError if not found."""
    # Check env override first
    vivado_path = os.getenv("VIVADO_PATH", "")
    if vivado_path:
        candidate = Path(vivado_path) / "bin" / "vivado"
        if candidate.exists():
            return str(candidate)

    if shutil.which("vivado"):
        return "vivado"

    raise RuntimeError(
        "vivado not found. Set VIVADO_PATH in .env or add Vivado/bin to PATH."
    )


async def run_batch(tcl_script: str, work_dir: str | None = None) -> dict:
    """Run Vivado in batch mode with the given TCL script.

    Returns:
        {
            "returncode": int,
            "stdout":     str,
            "stderr":     str,
            "work_dir":   str,
        }
    """
    vivado = _find_vivado()

    with tempfile.TemporaryDirectory() as tmp_dir:
        wd = work_dir or tmp_dir
        tcl_path = Path(wd) / "run.tcl"
        tcl_path.write_text(tcl_script)

        proc = await asyncio.create_subprocess_exec(
            vivado,
            "-mode", "batch",
            "-source", str(tcl_path),
            "-nojournal",
            "-nolog",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=wd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

        return {
            "returncode": proc.returncode,
            "stdout":     stdout.decode(),
            "stderr":     stderr.decode(),
            "work_dir":   wd,
        }


def build_synth_tcl(
    files: list[str],
    top_module: str,
    part: str = "xc7a35tcpg236-1",
) -> str:
    """Generate TCL script for synthesis."""
    # Group files by extension
    v_files  = [f for f in files if f.endswith((".v", ".sv"))]
    vhd_files = [f for f in files if f.endswith((".vhd", ".vhdl"))]

    read_cmds = "\n".join(
        [f"read_verilog -sv {{{f}}}" for f in v_files]
        + [f"read_vhdl {{{f}}}" for f in vhd_files]
    )

    return _SYNTH_TCL.format(
        part=part,
        read_cmds=read_cmds,
        top_module=top_module,
    )


def parse_timing_report(report_text: str) -> dict:
    """Extract WNS and TNS from Vivado timing summary report."""
    m = _TIMING_PARSE_RE.search(report_text)
    if m:
        return {"wns": float(m.group(1)), "tns": float(m.group(2))}
    return {"wns": None, "tns": None}


def parse_utilization_report(report_text: str) -> dict:
    """Extract LUT/FF/BRAM/DSP utilization from Vivado utilization report."""
    util: dict = {}
    resource_map = {
        "LUT as Logic":  "lut",
        "Flip Flop":     "ff",
        "Block RAM":     "bram",
        "DSPs":          "dsp",
    }
    for m in _UTIL_RE.finditer(report_text):
        resource = m.group(1).strip()
        for key, short in resource_map.items():
            if key.lower() in resource.lower():
                used      = int(m.group(2))
                available = int(m.group(3))
                util[short] = {
                    "used":      used,
                    "available": available,
                    "pct":       round(used / available * 100, 1) if available else 0,
                }
    return util


def parse_errors(stdout: str) -> list[dict]:
    """Parse Vivado stdout and classify errors by semantic category."""
    ERROR_PATTERNS = [
        ("Synth 8-439",   "missing_module",    "Check file list; verify module name spelling"),
        ("Synth 8-6014",  "latch_inferred",    "Add default branch to if/case statement"),
        ("Place 30-574",  "io_bufg_placement", "Consider IBUFG or adjust pin assignment"),
        ("Timing 38-282", "setup_violation",   "Insert pipeline register or reduce clock frequency"),
        ("Impl 41-186",   "congestion_high",   "Resource utilization too high; relax Pblock constraints"),
    ]
    results: list[dict] = []
    for code, category, suggestion in ERROR_PATTERNS:
        if code in stdout:
            results.append({
                "code":       code,
                "category":   category,
                "suggestion": suggestion,
            })
    # Also capture raw ERROR lines
    for line in stdout.splitlines():
        if line.startswith("ERROR:"):
            results.append({"raw": line.strip()})
    return results
