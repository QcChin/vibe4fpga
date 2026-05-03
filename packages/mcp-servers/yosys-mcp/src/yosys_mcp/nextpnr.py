"""nextpnr Place & Route wrapper.

Supports iCE40 and ECP5 targets and generates iCE40 bitstreams via
``icepack``. All subprocess invocations flow through
``vibe4fpga_platform.run`` for cross-platform behaviour and safe timeouts.
"""

from __future__ import annotations

import re
from pathlib import Path

from vibe4fpga_platform import (
    Completed,
    ProcessTimeoutError,
    find_tool,
    run,
)


# ── Parsing ───────────────────────────────────────────────────────────────────

_FMAX_RE     = re.compile(r"Max frequency for clock.*?:\s*([\d.]+)\s*MHz", re.IGNORECASE)
_UTIL_LUT_RE = re.compile(r"LCs used as LUT4\s+(\d+)/\s*(\d+)")
_UTIL_FF_RE  = re.compile(r"LCs used as FF\s+(\d+)/\s*(\d+)")
_UTIL_RAM_RE = re.compile(r"(?:BRAMs|Block RAMs) used\s+(\d+)/\s*(\d+)")
_UTIL_IO_RE  = re.compile(r"IOs used\s+(\d+)/\s*(\d+)")
_ERROR_RE    = re.compile(r"^ERROR:\s+(.+)", re.MULTILINE)
_WARNING_RE  = re.compile(r"^WARNING:\s+(.+)", re.MULTILINE)


def _parse_nextpnr_output(stdout: str, stderr: str) -> dict:
    text = stdout + "\n" + stderr
    errors   = [m.group(1).strip() for m in _ERROR_RE.finditer(text)]
    warnings = [m.group(1).strip() for m in _WARNING_RE.finditer(text)][:20]

    fmax_m     = _FMAX_RE.search(text)
    lut_m      = _UTIL_LUT_RE.search(text)
    ff_m       = _UTIL_FF_RE.search(text)
    ram_m      = _UTIL_RAM_RE.search(text)
    io_m       = _UTIL_IO_RE.search(text)

    def _pair(m, g1=1, g2=2):
        return (int(m.group(g1)), int(m.group(g2))) if m else (None, None)

    lut_used, lut_total = _pair(lut_m)
    ff_used, ff_total   = _pair(ff_m)
    ram_used, ram_total = _pair(ram_m)
    io_used, io_total   = _pair(io_m)

    return {
        "success":  len(errors) == 0,
        "errors":   errors,
        "warnings": warnings,
        "fmax_mhz": float(fmax_m.group(1)) if fmax_m else None,
        "utilization": {
            "lut":  {"used": lut_used, "total": lut_total},
            "ff":   {"used": ff_used,  "total": ff_total},
            "bram": {"used": ram_used, "total": ram_total},
            "io":   {"used": io_used,  "total": io_total},
        },
    }


# ── nextpnr runner ────────────────────────────────────────────────────────────

async def _run_nextpnr(
    cmd: list,
    work_dir: Path,
    timeout: int,
    binary_label: str,
) -> tuple[str, str, int]:
    try:
        completed: Completed = await run(cmd, cwd=work_dir, timeout=timeout)
    except ProcessTimeoutError:
        return "", f"{binary_label} timed out after {timeout}s", -1
    except FileNotFoundError as exc:
        return (
            "",
            f"{binary_label} not found: {exc}. Install nextpnr via "
            "`brew install nextpnr` (macOS) or oss-cad-suite (Windows).",
            -1,
        )
    return completed.stdout_text(), completed.stderr_text(), completed.returncode


async def place_and_route_ice40(
    netlist_json: str,
    pcf_file: str | None = None,
    device: str = "hx8k",
    package: str = "ct256",
    output_asc: str | None = None,
    freq_constraint_mhz: float | None = None,
    seed: int = 1,
    timeout: int = 300,
) -> dict:
    """Run nextpnr-ice40 Place & Route.

    Args:
        netlist_json:         Yosys JSON netlist.
        pcf_file:             Physical Constraints File with pin assignments.
        device:               iCE40 device (lp1k/lp8k/hx1k/hx8k/up5k/...).
        package:              Device package (ct256, bg48, ...).
        output_asc:           Output .asc file path (auto-generated if None).
        freq_constraint_mhz:  Target clock frequency for timing-driven PnR.
        seed:                 Random seed for reproducibility.

    Returns:
        {success, fmax_mhz, utilization, output_asc, errors, warnings}
    """
    nextpnr_bin = find_tool("nextpnr-ice40", env_var="NEXTPNR_ICE40_PATH")
    if nextpnr_bin is None:
        return {
            "success": False,
            "errors": [
                "nextpnr-ice40 not found. Install via "
                "`brew install nextpnr` (macOS) or oss-cad-suite (Windows)."
            ],
            "returncode": -1,
        }

    work_dir = Path(netlist_json).parent
    top = Path(netlist_json).stem.replace("_synth", "")
    asc_path = Path(output_asc) if output_asc else work_dir / f"{top}.asc"

    cmd: list = [
        nextpnr_bin,
        f"--{device}",
        "--package", package,
        "--json", netlist_json,
        "--asc", str(asc_path),
        "--seed", str(seed),
    ]
    if pcf_file:
        cmd += ["--pcf", pcf_file]
    if freq_constraint_mhz:
        cmd += ["--freq", str(freq_constraint_mhz)]

    stdout, stderr, rc = await _run_nextpnr(cmd, work_dir, timeout, "nextpnr-ice40")
    result = _parse_nextpnr_output(stdout, stderr)
    result["returncode"] = rc
    result["output_asc"] = str(asc_path) if asc_path.exists() else None
    result["log_excerpt"] = (stdout + stderr)[-2000:]
    return result


async def place_and_route_ecp5(
    netlist_json: str,
    lpf_file: str | None = None,
    device: str = "25k",
    package: str = "CABGA256",
    output_config: str | None = None,
    freq_constraint_mhz: float | None = None,
    seed: int = 1,
    timeout: int = 300,
) -> dict:
    """Run nextpnr-ecp5 Place & Route.

    Args:
        netlist_json:   Yosys JSON netlist.
        lpf_file:       LPF (Lattice Preference File) with pin assignments.
        device:         ECP5 device (25k/45k/85k).
        package:        Device package (CABGA256, CABGA381, ...).
        output_config:  Output .config file path (auto-generated if None).

    Returns:
        {success, fmax_mhz, utilization, output_config, errors, warnings}
    """
    nextpnr_bin = find_tool("nextpnr-ecp5", env_var="NEXTPNR_ECP5_PATH")
    if nextpnr_bin is None:
        return {
            "success": False,
            "errors": [
                "nextpnr-ecp5 not found. Install via "
                "`brew install nextpnr` (macOS) or oss-cad-suite (Windows)."
            ],
            "returncode": -1,
        }

    work_dir = Path(netlist_json).parent
    top = Path(netlist_json).stem.replace("_synth", "")
    cfg_path = Path(output_config) if output_config else work_dir / f"{top}.config"

    cmd: list = [
        nextpnr_bin,
        f"--{device}",
        "--package", package,
        "--json", netlist_json,
        "--textcfg", str(cfg_path),
        "--seed", str(seed),
    ]
    if lpf_file:
        cmd += ["--lpf", lpf_file]
    if freq_constraint_mhz:
        cmd += ["--freq", str(freq_constraint_mhz)]

    stdout, stderr, rc = await _run_nextpnr(cmd, work_dir, timeout, "nextpnr-ecp5")
    result = _parse_nextpnr_output(stdout, stderr)
    result["returncode"] = rc
    result["output_config"] = str(cfg_path) if cfg_path.exists() else None
    result["log_excerpt"] = (stdout + stderr)[-2000:]
    return result


async def generate_bitstream_ice40(asc_file: str, output_bin: str | None = None) -> dict:
    """Generate iCE40 bitstream from .asc using icepack (Icestorm).

    Args:
        asc_file:   Path to nextpnr output .asc file.
        output_bin: Output .bin file path (auto-generated if None).

    Returns:
        {success, output_bin}
    """
    icepack_bin = find_tool("icepack", env_var="ICEPACK_PATH")
    if icepack_bin is None:
        return {
            "success": False,
            "error": (
                "icepack not found. Install icestorm tools via "
                "`brew install icestorm` (macOS) or oss-cad-suite (Windows)."
            ),
        }

    work_dir = Path(asc_file).parent
    bin_file = Path(output_bin) if output_bin else Path(asc_file).with_suffix(".bin")

    try:
        completed = await run(
            [icepack_bin, asc_file, str(bin_file)],
            cwd=work_dir,
            timeout=30,
        )
    except ProcessTimeoutError:
        return {"success": False, "error": "icepack timed out"}

    stdout = completed.stdout_text()
    stderr = completed.stderr_text()
    rc = completed.returncode

    return {
        "success": rc == 0,
        "output_bin": str(bin_file) if bin_file.exists() else None,
        "returncode": rc,
        "output": (stdout + stderr)[-500:],
    }
