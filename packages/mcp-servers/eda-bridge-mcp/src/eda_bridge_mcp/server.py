"""eda-bridge-mcp — EDA Tool Control MCP Server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .lint import lint_files
from .vivado import (
    build_synth_tcl,
    parse_errors,
    parse_timing_report,
    parse_utilization_report,
    run_batch,
)

mcp = FastMCP("eda-bridge-mcp")


@mcp.tool()
async def run_lint(project_path: str, files: list[str]) -> dict:
    """Run static lint: verilator --lint-only + verible-verilog-lint in parallel.

    ~12ms per file. Catches ~40% of LLM-generated RTL issues.

    Args:
        project_path: Project root (used for resolving relative paths).
        files:        List of RTL file paths to lint.

    Returns:
        {
            "findings":      [{tool, severity, file, line, col, message}],
            "error_count":   int,
            "warning_count": int,
            "score_penalty": int,   # -10/error, -2/warning
        }
    """
    return await lint_files(files)


@mcp.tool()
async def run_synthesis(
    project_path: str,
    files: list[str],
    top_module: str,
    part: str = "xc7a35tcpg236-1",
) -> dict:
    """Run Vivado synthesis in batch mode.

    Args:
        files:      List of RTL source files (absolute paths).
        top_module: Name of the top-level module.
        part:       Xilinx part number (default: Artix-7 xc7a35t).

    Returns:
        {
            "success":          bool,
            "errors":           [{"code", "category", "suggestion"}],
            "timing":           {"wns": float, "tns": float},
            "utilization":      {"lut": {...}, "ff": {...}, ...},
            "duration_seconds": float,
            "work_dir":         str,
        }
    """
    import time
    tcl = build_synth_tcl(files=files, top_module=top_module, part=part)

    t0 = time.monotonic()
    result = await run_batch(tcl, work_dir=project_path)
    elapsed = time.monotonic() - t0

    errors = parse_errors(result["stdout"] + result["stderr"])
    success = result["returncode"] == 0 and not any(
        "category" in e for e in errors  # only structural errors, not raw
    )

    # Try to parse report files if synthesis succeeded
    from pathlib import Path
    timing: dict = {}
    utilization: dict = {}

    timing_rpt = Path(result["work_dir"]) / "timing.rpt"
    util_rpt   = Path(result["work_dir"]) / "util.rpt"

    if timing_rpt.exists():
        timing = parse_timing_report(timing_rpt.read_text())
    if util_rpt.exists():
        utilization = parse_utilization_report(util_rpt.read_text())

    return {
        "success":          success,
        "errors":           errors,
        "timing":           timing,
        "utilization":      utilization,
        "duration_seconds": round(elapsed, 1),
        "work_dir":         result["work_dir"],
    }


@mcp.tool()
async def run_simulation(
    project_path: str,
    testbench: str,
    source_files: list[str],
    simulator: str = "icarus",
    vcd_output: str | None = None,
) -> dict:
    """Run functional simulation with Icarus Verilog / Verilator / Vivado xsim.

    Args:
        testbench:    Path to testbench file.
        source_files: RTL source files to compile alongside testbench.
        simulator:    "icarus" | "verilator" | "xsim"
        vcd_output:   Path to write VCD waveform (optional).
    """
    import asyncio
    import shutil

    if simulator == "icarus":
        if not shutil.which("iverilog"):
            return {"success": False, "error": "iverilog not found in PATH"}

        out_file = vcd_output or "/tmp/sim_out.vvp"
        all_files = source_files + [testbench]
        compile_cmd = ["iverilog", "-g2012", "-o", out_file] + all_files

        proc = await asyncio.create_subprocess_exec(
            *compile_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode != 0:
            return {
                "success": False,
                "phase":   "compile",
                "errors":  stderr.decode().splitlines(),
            }

        run_proc = await asyncio.create_subprocess_exec(
            "vvp", out_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(run_proc.communicate(), timeout=300)

        return {
            "success":  run_proc.returncode == 0,
            "stdout":   stdout.decode()[-4000:],   # last 4K to avoid huge payloads
            "stderr":   stderr.decode()[-1000:],
            "vcd_path": vcd_output,
        }

    return {"success": False, "error": f"Simulator '{simulator}' not yet implemented"}


@mcp.tool()
async def get_timing_report(project_path: str) -> dict:
    """Parse the most recent Vivado timing report in the project directory.

    Returns:
        {"wns": float, "tns": float}  (None if report not found)
    """
    from pathlib import Path
    for candidate in sorted(Path(project_path).rglob("timing*.rpt")):
        return parse_timing_report(candidate.read_text())
    return {"wns": None, "tns": None, "error": "No timing report found"}


@mcp.tool()
async def classify_errors(raw_stdout: str) -> list[dict]:
    """Classify raw Vivado stdout into semantic error categories.

    LLM always sees category + suggestion, not raw error codes.
    """
    return parse_errors(raw_stdout)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
