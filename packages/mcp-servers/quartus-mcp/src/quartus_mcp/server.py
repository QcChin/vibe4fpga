"""quartus-mcp — Intel Quartus Prime MCP Server."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .qsf_manager import (
    QSFProject,
    add_source_file,
    generate_qsf,
    parse_qsf,
    set_pin_assignment,
)
from .tcl_runner import compile_project, program_device, run_timing_analysis, synthesize_only

mcp = FastMCP("quartus-mcp")

QUARTUS_SH = os.getenv("QUARTUS_SH", "quartus_sh")


# ── QSF management tools ──────────────────────────────────────────────────────

@mcp.tool()
async def read_qsf(qsf_file: str) -> dict:
    """Parse a Quartus Settings File (.qsf) and return project metadata.

    Args:
        qsf_file: Path to the .qsf file.

    Returns:
        {family, device, top_entity, source_files, pin_assignments, timing_constraints}
    """
    try:
        proj = parse_qsf(qsf_file)
        return {
            "family":        proj.family,
            "device":        proj.device,
            "top_entity":    proj.top_entity,
            "source_files":  proj.source_files,
            "pin_assignments": [
                {"signal": p.signal, "pin": p.pin, "io_standard": p.io_standard}
                for p in proj.pin_assignments
            ],
            "timing_constraints": proj.timing_constraints,
            "global_assignments": proj.global_assignments,
        }
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def create_qsf(
    output_path: str,
    family: str,
    device: str,
    top_entity: str,
    source_files: list[str] | None = None,
) -> dict:
    """Create a new Quartus Settings File (.qsf).

    Args:
        output_path: Where to write the .qsf file.
        family:      Device family (e.g., "Cyclone V", "MAX 10").
        device:      Full part number (e.g., "5CSEBA6U23I7").
        top_entity:  Top-level module name.
        source_files: List of HDL source file paths to include.

    Returns:
        {"created": true, "path": str, "content_preview": str}
    """
    proj = QSFProject(
        family=family,
        device=device,
        top_entity=top_entity,
        source_files=source_files or [],
    )
    content = generate_qsf(proj)
    Path(output_path).write_text(content)
    return {"created": True, "path": output_path, "content_preview": content[:500]}


@mcp.tool()
async def add_hdl_file(qsf_file: str, hdl_path: str) -> dict:
    """Add an HDL source file to an existing QSF project.

    Args:
        qsf_file: Path to the existing .qsf file.
        hdl_path: Path to the Verilog/SystemVerilog/VHDL file to add.

    Returns:
        {"updated": true, "source_files": [...]}
    """
    try:
        new_content = add_source_file(qsf_file, hdl_path)
        Path(qsf_file).write_text(new_content)
        proj = parse_qsf(qsf_file)
        return {"updated": True, "source_files": proj.source_files}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def assign_pin(
    qsf_file: str,
    signal: str,
    pin: str,
    io_standard: str = "3.3-V LVTTL",
) -> dict:
    """Set or update a pin assignment in a QSF file.

    Args:
        qsf_file:    Path to the .qsf file.
        signal:      HDL signal/port name (e.g., "clk", "led[0]").
        pin:         FPGA pin name (e.g., "PIN_M9", "PIN_AA14").
        io_standard: I/O standard (e.g., "3.3-V LVTTL", "LVDS", "SSTL-15").

    Returns:
        {"updated": true, "pin_assignments": [...]}
    """
    try:
        new_content = set_pin_assignment(qsf_file, signal, pin, io_standard)
        Path(qsf_file).write_text(new_content)
        proj = parse_qsf(qsf_file)
        return {
            "updated": True,
            "pin_assignments": [
                {"signal": p.signal, "pin": p.pin, "io_standard": p.io_standard}
                for p in proj.pin_assignments
            ],
        }
    except Exception as exc:
        return {"error": str(exc)}


# ── Compilation tools ─────────────────────────────────────────────────────────

@mcp.tool()
async def compile_quartus_project(
    project_dir: str,
    project_name: str,
    revision: str = "",
    timeout: int = 600,
) -> dict:
    """Run full Quartus compilation (Analysis→Synthesis→Fit→Assemble→Timing).

    Args:
        project_dir:  Directory containing the .qpf/.qsf files.
        project_name: Quartus project name (without .qpf extension).
        revision:     Project revision name (defaults to project_name).
        timeout:      Max compilation time in seconds.

    Returns:
        {success, errors, warnings, fmax_mhz, worst_slack_ns, utilization, log_excerpt}
    """
    return await compile_project(
        project_dir=project_dir,
        project_name=project_name,
        revision=revision,
        quartus_sh=QUARTUS_SH,
        timeout=timeout,
    )


@mcp.tool()
async def synthesize_quartus(
    project_dir: str,
    project_name: str,
    revision: str = "",
    timeout: int = 300,
) -> dict:
    """Run Analysis & Synthesis only (fast, no P&R).

    Useful for quick error checking without full compilation.

    Returns:
        {success, errors, warnings, log_excerpt}
    """
    return await synthesize_only(
        project_dir=project_dir,
        project_name=project_name,
        revision=revision,
        quartus_sh=QUARTUS_SH,
        timeout=timeout,
    )


@mcp.tool()
async def get_timing_report(
    project_dir: str,
    project_name: str,
    revision: str = "",
    timeout: int = 120,
) -> dict:
    """Run TimeQuest Timing Analysis and return Fmax/slack summary.

    Returns:
        {fmax_mhz, worst_slack_ns, timing_report, fmax_report}
    """
    return await run_timing_analysis(
        project_dir=project_dir,
        project_name=project_name,
        revision=revision,
        quartus_sh=QUARTUS_SH,
        timeout=timeout,
    )


@mcp.tool()
async def program_fpga(sof_file: str, timeout: int = 60) -> dict:
    """Download .sof bitfile to FPGA device via USB-Blaster JTAG.

    Args:
        sof_file: Path to the .sof output file from Quartus compilation.
        timeout:  Programming timeout in seconds.

    Returns:
        {success, returncode, output}
    """
    return await program_device(sof_file=sof_file, quartus_sh=QUARTUS_SH, timeout=timeout)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
