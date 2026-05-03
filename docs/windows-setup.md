# Windows setup

The MCPs are developed on macOS and tested on macOS **and** Windows CI,
but the production target is Windows. A few Windows-specific settings
matter.

## Python

Install Python **3.11 or 3.12**. 3.13 is not yet supported by the `mcp`
SDK (`requires-python = ">=3.11,<3.13"`).

```powershell
winget install -e --id Python.Python.3.12
```

If you have both 3.11 and 3.12 installed, `uv tool install` may pick the
wrong one. Lock it:

```powershell
uv tool install --python 3.12 fpga-project-mcp
```

## UTF-8 console (critical on CN Windows)

CN / JP / KR Windows defaults to a GBK / SJIS / CP949 console. Without a
UTF-8 setting, subprocess stdout from EDA tools gets garbled or truncated.

`vibe4fpga-platform` already forces `PYTHONIOENCODING=utf-8` and
`PYTHONUTF8=1` in child processes of `platform.run()`. For the agent host
itself (Claude Code / Codex / OpenCode) you also want:

```powershell
# One-off, current PowerShell session
chcp 65001
$env:PYTHONUTF8 = "1"

# Persistent (user env)
setx PYTHONUTF8 1
```

## Long path support

Vivado and other Xilinx tools produce deep build directories that bump
against Windows' default 260-character path limit.

* **Group Policy**: enable `Computer Configuration → Administrative Templates
  → System → Filesystem → Enable Win32 long paths`.
* **Registry** alternative:
  ```powershell
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
                   -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
  ```
* Reboot (Group Policy) or log out/in (registry).

`vibe4fpga-platform.long_path()` also prefixes paths with `\\?\` when
they exceed 240 chars, but some downstream tools (and Python itself before
3.6) ignore the prefix — enabling the policy is the reliable fix.

## Vivado

Install Vivado Design Suite to the default location and then either:

* Set `VIVADO_ROOT` to the Vivado install root (e.g. `C:\Xilinx\Vivado\2024.2`):
  ```powershell
  setx VIVADO_ROOT "C:\Xilinx\Vivado\2024.2"
  ```
  `eda-bridge-mcp` picks this up via `find_tool(..., extra_paths=[VIVADO_ROOT/bin, ...])`.
* Or add the Vivado `bin` directory to your `PATH`.

## Quartus Prime

```powershell
setx QUARTUS_ROOTDIR "C:\intelFPGA_lite\23.1\quartus"
# Optional: point directly at quartus_sh if it's not on PATH
setx QUARTUS_SH "C:\intelFPGA_lite\23.1\quartus\bin64\quartus_sh.exe"
```

`quartus-mcp` detects Pro vs Lite edition at startup and surfaces it via
the `quartus_environment` tool. If the tool reports `Unknown`, check that
`quartus_sh --version` works in your terminal.

## Yosys / nextpnr / icepack

Use [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build):

```powershell
# Download and extract oss-cad-suite-windows-x64.exe
$env:PATH += ";C:\oss-cad-suite\bin"
```

Or per-tool overrides:

```powershell
setx YOSYS_PATH        "C:\oss-cad-suite\bin\yosys.exe"
setx NEXTPNR_ICE40_PATH "C:\oss-cad-suite\bin\nextpnr-ice40.exe"
setx NEXTPNR_ECP5_PATH  "C:\oss-cad-suite\bin\nextpnr-ecp5.exe"
setx ICEPACK_PATH       "C:\oss-cad-suite\bin\icepack.exe"
```

## VISA drivers (instrument-mcp)

`pyvisa` needs a vendor backend installed. Pick one:

* **NI-VISA** ([download](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html))
  — most common, works with Rigol, Keysight, Tektronix scopes.
* **Keysight I/O Libraries Suite** — preferred with Keysight hardware.
* **pyvisa-py** — pure-Python backend, no admin required, limited
  protocol support: `uv pip install pyvisa-py pyserial pyusb`.

`list_visa_instruments` returns a structured error (not a crash) when no
backend is available, so you can tell at a glance which driver to install.

## Rust toolchain (for `waveform-mcp-rs` from source)

Install via `rustup`:

```powershell
winget install -e --id Rustlang.Rustup
rustup default stable
```

You'll also need the **MSVC build tools** (VS 2019+ Build Tools).
Alternative: download prebuilt `waveform-mcp-rs-windows-x86_64.zip`
binaries from GitHub Releases and drop the `.exe` on your PATH.

## Verify setup

From a fresh PowerShell:

```powershell
uv --version
python --version            # 3.11.x or 3.12.x
fpga-project-mcp --help 2>&1 | Select-Object -First 5
vivado -version             # if you installed it
quartus_sh --version        # if you installed it
yosys --version             # if you installed it
```

If any of those fail, check the per-MCP README for the exact env var it
looks for.
