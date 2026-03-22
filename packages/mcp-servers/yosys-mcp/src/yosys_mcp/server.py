"""yosys-mcp — Yosys/nextpnr open-source FPGA toolchain MCP Server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .nextpnr import (
    generate_bitstream_ice40,
    place_and_route_ecp5,
    place_and_route_ice40,
)
from .synth import prepare_formal, synthesize

mcp = FastMCP("yosys-mcp")


# ── Synthesis tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def synthesize_ice40(
    source_files: list[str],
    top_module: str,
    work_dir: str | None = None,
    sv_mode: bool = False,
    extra_flags: str = "",
) -> dict:
    """Synthesize Verilog/SV for iCE40 target using Yosys.

    Args:
        source_files: HDL source file paths.
        top_module:   Top-level module name.
        work_dir:     Output directory (temp dir if None).
        sv_mode:      Enable SystemVerilog parsing.
        extra_flags:  Extra synth_ice40 flags (e.g. "-abc9 -dffe_min_ce_use 4").

    Returns:
        {success, errors, warnings, cells, output_json}
    """
    return await synthesize(
        source_files=source_files,
        top_module=top_module,
        target="ice40",
        work_dir=work_dir,
        sv_mode=sv_mode,
        extra_flags=extra_flags,
    )


@mcp.tool()
async def synthesize_ecp5(
    source_files: list[str],
    top_module: str,
    work_dir: str | None = None,
    sv_mode: bool = False,
    extra_flags: str = "",
) -> dict:
    """Synthesize Verilog/SV for ECP5 target using Yosys.

    Returns:
        {success, errors, warnings, cells, output_json}
    """
    return await synthesize(
        source_files=source_files,
        top_module=top_module,
        target="ecp5",
        work_dir=work_dir,
        sv_mode=sv_mode,
        extra_flags=extra_flags,
    )


@mcp.tool()
async def synthesize_generic(
    source_files: list[str],
    top_module: str,
    work_dir: str | None = None,
    sv_mode: bool = False,
) -> dict:
    """Generic Yosys synthesis (technology-independent netlist).

    Useful for simulation, formal verification setup, or ASIC flow.

    Returns:
        {success, errors, warnings, cells, output_json}
    """
    return await synthesize(
        source_files=source_files,
        top_module=top_module,
        target="generic",
        work_dir=work_dir,
        sv_mode=sv_mode,
    )


@mcp.tool()
async def prepare_formal_verification(
    source_files: list[str],
    top_module: str,
    work_dir: str | None = None,
) -> dict:
    """Prepare SMT2 file for SymbiYosys formal verification.

    Args:
        source_files: SystemVerilog source files with SVA assertions.
        top_module:   Top-level module name.
        work_dir:     Output directory.

    Returns:
        {success, errors, output_smt2}
    """
    return await prepare_formal(
        source_files=source_files,
        top_module=top_module,
        work_dir=work_dir,
    )


# ── Place & Route tools ───────────────────────────────────────────────────────

@mcp.tool()
async def pnr_ice40(
    netlist_json: str,
    pcf_file: str | None = None,
    device: str = "hx8k",
    package: str = "ct256",
    freq_constraint_mhz: float | None = None,
    seed: int = 1,
) -> dict:
    """Place & Route for iCE40 using nextpnr-ice40.

    Args:
        netlist_json:          Yosys synthesis JSON output.
        pcf_file:              Physical Constraints File (.pcf) for pin assignment.
        device:                iCE40 variant (lp1k/lp8k/hx1k/hx8k/up5k/u4k).
        package:               Device package identifier.
        freq_constraint_mhz:   Target Fmax for timing-driven PnR.
        seed:                  Random seed.

    Returns:
        {success, fmax_mhz, utilization, output_asc, errors}
    """
    return await place_and_route_ice40(
        netlist_json=netlist_json,
        pcf_file=pcf_file,
        device=device,
        package=package,
        freq_constraint_mhz=freq_constraint_mhz,
        seed=seed,
    )


@mcp.tool()
async def pnr_ecp5(
    netlist_json: str,
    lpf_file: str | None = None,
    device: str = "25k",
    package: str = "CABGA256",
    freq_constraint_mhz: float | None = None,
    seed: int = 1,
) -> dict:
    """Place & Route for ECP5 using nextpnr-ecp5.

    Args:
        netlist_json:          Yosys synthesis JSON output.
        lpf_file:              Lattice Preference File for pin assignment.
        device:                ECP5 device size (25k/45k/85k).
        package:               Device package identifier.
        freq_constraint_mhz:   Target Fmax.

    Returns:
        {success, fmax_mhz, utilization, output_config, errors}
    """
    return await place_and_route_ecp5(
        netlist_json=netlist_json,
        lpf_file=lpf_file,
        device=device,
        package=package,
        freq_constraint_mhz=freq_constraint_mhz,
        seed=seed,
    )


@mcp.tool()
async def pack_ice40_bitstream(asc_file: str, output_bin: str | None = None) -> dict:
    """Pack iCE40 .asc into binary bitstream using icepack.

    Args:
        asc_file:   nextpnr .asc output file.
        output_bin: Output .bin path (same dir as asc_file if None).

    Returns:
        {success, output_bin}
    """
    return await generate_bitstream_ice40(asc_file=asc_file, output_bin=output_bin)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
