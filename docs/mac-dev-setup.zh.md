# Mac 开发环境（瞄准 Windows 目标）

> 🌐 **中文** · [English](mac-dev-setup.md)

MCP 在生产上跑在 Windows，但日常开发在 macOS 上。这份文档说明怎么在 Mac 上写代码、跑测试，同时把 Windows-specific 的行为覆盖到位。

## 基础安装

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.12
brew install python@3.12

# 共享库 + 全部 8 个 MCP 以 editable 模式装好
make install

# 验证
make test
```

Apple Silicon 和 Intel 都行；CI 跑的是 `macos-latest`（Apple Silicon）。

## Mac 上能原生装的工具

| 工具 | 安装命令 | 备注 |
| --- | --- | --- |
| Yosys + nextpnr + icepack | `brew install yosys nextpnr icestorm` | 完全支持 |
| Icarus Verilog | `brew install icarus-verilog` | 完全支持 |
| Verilator | `brew install verilator` | 完全支持 |
| pyvisa（只有 py 后端） | `uv pip install pyvisa pyvisa-py` | SCPI 协议覆盖有限 |
| Rust stable | `brew install rustup-init && rustup-init` | `waveform-mcp-rs` 要用 |

## Mac 上跑不了的工具

* **Vivado** —— 没 macOS 版。用虚拟机（UTM + Windows 11 在 Apple Silicon 上能跑）、一台 Windows 开发机，或者在受影响的测试里把工具调用 stub 掉。
* **Quartus Prime** —— 同上（仅 Linux / Windows）。CI 已经把 `quartus-mcp × macos-latest` 排除掉；本地 `make test` 也会 skip Quartus-gated 测试。
* **NI-VISA / Keysight VISA** —— 仪器后端仅 Windows / Linux。pyvisa-py 能补一部分（Python 原生 serial / socket），但厂家示波器控制通常得装 OEM 驱动。

## 按工具可用性闸控测试

所有 MCP 对真正调外部工具的 Tier 3 测试都用一样的 `@pytest.mark.skipif(not find_tool("..."), reason=...)` 模板。Mac 干净安装下大部分 skip 得很干净 —— Tier 1 / Tier 2 套件依然能验证最重要的代码路径。

例子（`quartus-mcp`）：

```python
from vibe4fpga_platform import find_tool

@pytest.mark.skipif(
    not find_tool("quartus_sh", env_var="QUARTUS_SH"),
    reason="Quartus absent on runner",
)
async def test_quartus_environment(...): ...
```

## 跨平台经验法则

写新代码时多用 `vibe4fpga-platform`，让同一份源码在两个 OS 都能跑。共享 helper 专门解决的几类模式：

| 别写 | 写这个 | 为什么 |
| --- | --- | --- |
| `"/tmp/foo.vvp"` | `scratch_file(".vvp")` | Windows 没有 `/tmp` |
| `tempfile.NamedTemporaryFile` | `scratch_file` / `scratch_dir` | 标准库的版本会在 GC 时删掉文件；`scratch_dir` 注册一次性的 at-exit 清理 |
| `shutil.which("vivado")` | `find_tool("vivado", env_var="VIVADO_ROOT", extra_paths=[...])` | 处理 Windows `PATHEXT` 和厂商安装提示目录 |
| `asyncio.create_subprocess_exec(...)` | `await platform.run(cmd, timeout=...)` | 给子进程强制 UTF-8 环境；Windows 长路径加 `\\?\` 前缀；超时统一升级成类型化错误 |
| 字符串拼路径 | `Path(a) / b` | 避免 `/` vs `\` 分隔符的坑 |

CI 闸 `rg '"/tmp' packages/` 会卡第一种；剩下的在 Windows 上会静默挂、Mac 上又会通过 —— 主要靠自律。

## 在本地跑 Windows CI（用 act）

想推之前先在本地冒烟 PR 的话，[act](https://github.com/nektos/act) 在本地容器里跑 GitHub Actions：

```bash
brew install act
act -j python-test -P windows-latest=windows-latest   # 会 skip —— act 不模拟 Windows
act -j python-test -P macos-latest=-self-hosted       # 跑 macOS 这条腿
```

完整的 Windows 覆盖还是得在 GitHub 托管 runner 上跑 —— 没办法在 Mac 上实际测 Windows 那种 `\\?\` 长路径或 GBK 控制台行为。

## 编辑器配置

VS Code 或 Cursor 装：

* **Python extension**（Microsoft 或 Anysphere）
* **Ruff extension** —— 我们用 ruff lint；提交前每个包跑一次 `uv run ruff check`
* **rust-analyzer** 给唯一一个 Rust crate

每个 Python 包有自己的 pyproject + 在 `.venv/` 下的 uv venv；让编辑器在打开某个 MCP 子目录里的文件时挑该 MCP 的 venv（大多数扩展通过 `python.venvPath` 或 per-folder 设置做这件事）。

## 通过 stdio 调试 MCP

`vibe4fpga-mcp-testkit` 在进程内 spawn binary，所以单个 pytest 就能跑完整的握手 + 工具调用：

```python
import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio

async def test_round_trip():
    async with stdio_server_spawn("fpga-project-mcp") as client:
        tools = await client.list_tools_names()
        result = await client.call_tool("scan_project", {"project_path": "..."})
        assert not result.isError
```

想交互式调试就直接跑 MCP，往里管道塞 JSON-RPC 行：

```bash
cd packages/mcp-servers/fpga-project-mcp
echo '{"jsonrpc":"2.0","id":1,"method":"initialize",...}' | uv run fpga-project-mcp
```

或者用 [MCP Inspector](https://github.com/modelcontextprotocol/inspector) 在 stdio 上加一层 GUI：

```bash
npx @modelcontextprotocol/inspector fpga-project-mcp
```
