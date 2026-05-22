# 接入 —— Claude Code

> 🌐 **中文** · [English](integration-claude-code.md)

## 前置条件

* **uv** 0.4+（`curl -LsSf https://astral.sh/uv/install.sh | sh`）
* **Python** 3.11 或 3.12（`mcp` SDK 还不支持 3.13）
* **Claude Code**（[安装](https://claude.com/claude-code)）
* 可选：跑 `waveform-mcp-rs` 需要 **Rust stable**；跑 eda-bridge / quartus / yosys / instrument 这几个 MCP 需要对应的 **Vivado / Quartus / Yosys / pyvisa**

## 装上各 MCP

仓库根目录下：

```bash
make install
```

或者逐个 MCP，在每个包目录内：

```bash
cd packages/mcp-servers/fpga-project-mcp
uv sync --all-extras
uv tool install .   # 把 `fpga-project-mcp` 暴露到 PATH
```

把另外 7 个想用的 MCP 重复一遍。

## 配置 `~/.claude.json`

生成器在 [`configs/claude-mcp-config.json`](../configs/claude-mcp-config.json) 留了即贴即用的片段。把它的 `mcpServers` 对象合并到你的宿主配置：

```json
{
  "mcpServers": {
    "fpga-project": { "command": "fpga-project-mcp",
                      "env": { "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}" } },
    "eda-bridge":   { "command": "eda-bridge-mcp",
                      "env": { "VIVADO_ROOT": "C:\\Xilinx\\Vivado\\2024.2" } },
    "waveform":     { "command": "waveform-mcp-rs",
                      "env": { "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}" } },
    "instrument":   { "command": "instrument-mcp",
                      "env": { "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}" } },
    "datasheet":    { "command": "datasheet-mcp",
                      "env": { "OPENAI_API_KEY": "${env:OPENAI_API_KEY}" } },
    "quartus":      { "command": "quartus-mcp",
                      "env": { "QUARTUS_ROOTDIR": "C:\\intelFPGA_lite\\23.1\\quartus" } },
    "yosys":        { "command": "yosys-mcp" },
    "verify":       { "command": "verify-mcp",
                      "env": { "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}" } }
  }
}
```

Windows 上文件在 `%USERPROFILE%\.claude.json`；macOS 上在 `~/.claude.json`。`${env:...}` 这种环境变量插值两边都好使。

## 技能（Skills）

仓库带一份 `.claude/skills/` 目录，每个 LLM 驱动的工具对应一个 SKILL.md。Claude Code 能自动发现——前提是它能看到这个目录（比如把仓库 clone 到你的工程里，或者把 `.claude/skills/` 软链到你的目标工程）。

可用技能：

| 技能文件 | 后端 MCP | 触发意图 |
| --- | --- | --- |
| `spec2rtl/SKILL.md` | fpga-project-mcp | "spec"、"rtl"、"verilog from english" |
| `code_review/SKILL.md` | fpga-project-mcp | "review"、"lint"、"cdc"、"latch" |
| `timing_fix/SKILL.md` | fpga-project-mcp | "timing"、"slack"、"wns"、"critical path" |
| `waveform_debug/SKILL.md` | waveform-mcp-rs | "waveform"、"vcd"、"glitch"、"stall" |
| `instrument_analyze/SKILL.md` | instrument-mcp | "oscilloscope"、"scope"、"diff classify" |
| `testbench_gen/SKILL.md` | verify-mcp | "testbench"、"tb"、"sim driver" |
| `verification/SKILL.md` | verify-mcp | "verify"、"score"、"verification report" |

## 验证

重启 Claude Code，打开一段对话，跑 `/mcp`：

```
mcp servers:
  fpga-project ✓ connected
  eda-bridge   ✓ connected
  waveform     ✓ connected
  ...
```

再试一个匹配某个技能触发词的 prompt，比如：

> Write an RTL module for a 4-bit saturating counter with asynchronous reset. Use spec2rtl.

Claude Code 应该会调 `fpga-project-mcp.spec_to_rtl`，把生成的 Verilog 加自检报告流式输出回来。

## 排错

| 现象 | 原因 | 修法 |
| --- | --- | --- |
| `fpga-project ✗ failed to start` | MCP 不在 PATH 上 | 在该包目录跑 `uv tool install`，再重启 Claude Code |
| `AuthError: ANTHROPIC_API_KEY is not set` | MCP 的 `env` 块里没注入 | 加上 `"env": {"ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}"}` |
| 工具返回 `ToolNotFoundError: vivado` | Vivado 不在 PATH，或者 `VIVADO_ROOT` 没设 | 在 `env` 块里设 `VIVADO_ROOT`，或者把 Vivado 的 `bin/` 加到 PATH |
| 工具输出里出现中文乱码 | 中文 Windows 的 GBK 控制台 | 已经被 `vibe4fpga-platform` 接管；要是还有问题看 [`windows-setup.md`](windows-setup.md) |
| 斜杠菜单里看不到技能 | `.claude/skills/` 不在 Claude Code 当前看到的工程里 | 把本仓库 clone 进 workspace，或者把 `.claude/skills/` 复制到你工程根目录 |
