"""Vivado process control — batch mode synthesis.

Two process communication modes are supported at the moment:
  Batch mode      (``vivado -mode batch``) : synthesis / implementation
  Persistent mode (``vivado -mode tcl``)   : reserved for interactive queries

Subprocess + scratch paths + tool discovery route through
:mod:`vibe4fpga_platform` so Windows hosts get UTF-8 console, long-path
prefixing, and ``.exe`` fallback without per-tool care.
"""

from __future__ import annotations

import re
from pathlib import Path

from vibe4fpga_platform import (
    ProcessTimeoutError,
    ToolNotFoundError,
    require_tool,
    run,
    scratch_dir,
)

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


def _find_vivado() -> Path:
    """Locate the Vivado executable; raise :class:`RuntimeError` if missing.

    Resolution order (via :func:`vibe4fpga_platform.require_tool`):
        1. ``VIVADO_ROOT`` env var — if it points directly at the ``vivado``
           binary, that path wins. If it points at an install root instead,
           the sibling ``bin`` directory is added to the search path.
        2. ``VIVADO_PATH`` env var (legacy) — treated as an install root whose
           ``bin`` subdir is prepended to ``PATH``.
        3. System ``PATH`` (with ``.exe`` fallback on Windows).
    """
    import os

    extra_paths: list[Path] = []
    for env_var in ("VIVADO_ROOT", "VIVADO_PATH"):
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            continue
        candidate = Path(raw)
        # Accept either the install root or the bin dir.
        if (candidate / "bin").is_dir():
            extra_paths.append(candidate / "bin")
        else:
            extra_paths.append(candidate)

    try:
        return require_tool("vivado", extra_paths=extra_paths or None)
    except ToolNotFoundError as exc:
        raise RuntimeError(
            "vivado not found. Set VIVADO_ROOT to the Vivado install root or "
            "add Vivado/bin to PATH."
        ) from exc


async def run_batch(tcl_script: str, work_dir: str | None = None) -> dict:
    """Run Vivado in batch mode with the given TCL script.

    Args:
        tcl_script: Full TCL source. Written to ``run.tcl`` inside ``work_dir``.
        work_dir:   Optional directory to execute in. When omitted a fresh
                    scratch directory is allocated via
                    :func:`vibe4fpga_platform.scratch_dir` (auto-cleaned on
                    process exit).

    Returns:
        {
            "returncode": int,
            "stdout":     str,
            "stderr":     str,
            "work_dir":   str,   # absolute path — reports live here too
        }
    """
    vivado = _find_vivado()

    wd = Path(work_dir) if work_dir else scratch_dir("vibe4fpga_vivado_")
    wd.mkdir(parents=True, exist_ok=True)

    tcl_path = wd / "run.tcl"
    tcl_path.write_text(tcl_script, encoding="utf-8")

    cmd = [
        vivado,
        "-mode", "batch",
        "-source", str(tcl_path),
        "-nojournal",
        "-nolog",
    ]

    try:
        result = await run(cmd, cwd=wd, timeout=600)
    except ProcessTimeoutError as exc:
        return {
            "returncode": -1,
            "stdout":     "",
            "stderr":     f"vivado timed out after {exc.timeout}s",
            "work_dir":   str(wd),
        }

    return {
        "returncode": result.returncode,
        "stdout":     result.stdout_text(),
        "stderr":     result.stderr_text(),
        "work_dir":   str(wd),
    }


def build_synth_tcl(
    files: list[str],
    top_module: str,
    part: str = "xc7a35tcpg236-1",
) -> str:
    """Generate TCL script for synthesis."""
    # Group files by extension
    v_files   = [f for f in files if f.endswith((".v", ".sv"))]
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
    """Extract WNS and TNS from a Vivado timing summary report."""
    m = _TIMING_PARSE_RE.search(report_text)
    if m:
        return {"wns": float(m.group(1)), "tns": float(m.group(2))}
    return {"wns": None, "tns": None}


def parse_utilization_report(report_text: str) -> dict:
    """Extract LUT/FF/BRAM/DSP utilization from a Vivado utilization report."""
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
    # Also capture raw ERROR lines.
    for line in stdout.splitlines():
        if line.startswith("ERROR:"):
            results.append({"raw": line.strip()})
    return results
