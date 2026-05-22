# Windows 系统准备

> 🌐 **中文** · [English](windows-setup.md)

MCP 在 macOS 上开发，在 macOS **和** Windows 两地 CI 测试，但生产目标是 Windows。有几个 Windows-specific 设置要注意。

## Python

装 Python **3.11 或 3.12**。3.13 还没被 `mcp` SDK 支持（`requires-python = ">=3.11,<3.13"`）。

```powershell
winget install -e --id Python.Python.3.12
```

如果同时装了 3.11 和 3.12，`uv tool install` 可能挑错。锁住版本：

```powershell
uv tool install --python 3.12 fpga-project-mcp
```

## UTF-8 控制台（中文 Windows 关键）

中 / 日 / 韩文 Windows 默认是 GBK / SJIS / CP949 控制台。不设 UTF-8 的话，EDA 工具的 subprocess stdout 会被乱码或截断。

`vibe4fpga-platform` 已经在 `platform.run()` 的子进程里强制 `PYTHONIOENCODING=utf-8` 和 `PYTHONUTF8=1`。Agent 宿主本身（Claude Code / Codex / OpenCode）也建议设置：

```powershell
# 一次性，当前 PowerShell session
chcp 65001
$env:PYTHONUTF8 = "1"

# 持久化（用户环境）
setx PYTHONUTF8 1
```

## 长路径支持

Vivado 和其他 Xilinx 工具会产生很深的构建目录，撞上 Windows 默认 260 字符路径限。

* **组策略**：开启 `计算机配置 → 管理模板 → 系统 → 文件系统 → 启用 Win32 长路径`。
* **注册表** 备选：
  ```powershell
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
                   -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
  ```
* 重启（组策略）或注销重登（注册表）。

`vibe4fpga-platform.long_path()` 也会在路径超 240 字符时加 `\\?\` 前缀，但部分下游工具（以及 3.6 之前的 Python）会忽略这个前缀 —— 开启策略才是稳定的修法。

## Vivado

把 Vivado Design Suite 装到默认位置，然后任选其一：

* 把 `VIVADO_ROOT` 设到 Vivado 安装根（例如 `C:\Xilinx\Vivado\2024.2`）：
  ```powershell
  setx VIVADO_ROOT "C:\Xilinx\Vivado\2024.2"
  ```
  `eda-bridge-mcp` 通过 `find_tool(..., extra_paths=[VIVADO_ROOT/bin, ...])` 捕获。
* 或者把 Vivado 的 `bin` 目录加到 `PATH`。

## Quartus Prime

```powershell
setx QUARTUS_ROOTDIR "C:\intelFPGA_lite\23.1\quartus"
# 可选：如果 quartus_sh 不在 PATH 上，直接指向它
setx QUARTUS_SH "C:\intelFPGA_lite\23.1\quartus\bin64\quartus_sh.exe"
```

`quartus-mcp` 启动时检测 Pro vs Lite 版本，通过 `quartus_environment` 工具暴露。要是这个工具汇报 `Unknown`，先在终端验证 `quartus_sh --version` 能正常跑。

## Yosys / nextpnr / icepack

用 [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build)：

```powershell
# 下载并解压 oss-cad-suite-windows-x64.exe
$env:PATH += ";C:\oss-cad-suite\bin"
```

或者按工具单独 override：

```powershell
setx YOSYS_PATH        "C:\oss-cad-suite\bin\yosys.exe"
setx NEXTPNR_ICE40_PATH "C:\oss-cad-suite\bin\nextpnr-ice40.exe"
setx NEXTPNR_ECP5_PATH  "C:\oss-cad-suite\bin\nextpnr-ecp5.exe"
setx ICEPACK_PATH       "C:\oss-cad-suite\bin\icepack.exe"
```

## VISA 驱动（instrument-mcp）

`pyvisa` 需要装一个厂商后端，三选一：

* **NI-VISA**（[下载](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html)）—— 最通用，Rigol、Keysight、Tektronix 示波器都能用。
* **Keysight I/O Libraries Suite** —— Keysight 硬件首选。
* **pyvisa-py** —— 纯 Python 后端，不需要管理员权限，但协议覆盖有限：`uv pip install pyvisa-py pyserial pyusb`。

`list_visa_instruments` 在没装后端时返回结构化错误（不会崩），所以一眼能看出该装哪个驱动。

## Rust 工具链（从源码构建 `waveform-mcp-rs`）

通过 `rustup` 装：

```powershell
winget install -e --id Rustlang.Rustup
rustup default stable
```

还需要 **MSVC 构建工具**（VS 2019+ Build Tools）。备选方案：从 GitHub Releases 下载预编译的 `waveform-mcp-rs-windows-x86_64.zip`，把 `.exe` 放进 PATH。

## 验证

从一个新开的 PowerShell：

```powershell
uv --version
python --version            # 3.11.x 或 3.12.x
fpga-project-mcp --help 2>&1 | Select-Object -First 5
vivado -version             # 装了的话
quartus_sh --version        # 装了的话
yosys --version             # 装了的话
```

如果上面任意一项失败，查对应 MCP 的 README 里它找的具体环境变量名。
