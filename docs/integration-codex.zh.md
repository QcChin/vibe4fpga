# 接入 —— Codex CLI

> 🌐 **中文** · [English](integration-codex.md)

## 前置条件

* **uv** 0.4+
* **Python** 3.11 或 3.12
* **Codex CLI**（`npm install -g @openai/codex` 之类——以官方文档为准）
* 可选：跑 `waveform-mcp-rs` 需要 **Rust stable**；跑 eda-bridge / quartus / yosys / instrument 需要对应 **Vivado / Quartus / Yosys / pyvisa**

## 装上各 MCP

跟 Claude Code 一样：

```bash
make install
```

或者逐个 MCP：在每个包目录跑 `uv tool install .`，把入口塞到 PATH。

## 配置 `~/.codex/config.toml`

生成器把即贴即用的片段写在 [`configs/codex-config.toml`](../configs/codex-config.toml)。追加到你的 Codex 配置（Windows 路径：`%USERPROFILE%\.codex\config.toml`）：

```toml
[mcp_servers.fpga-project-mcp]
command = "fpga-project-mcp"
# 可选环境变量：VIBE4FPGA_LLM、ANTHROPIC_API_KEY、OPENAI_API_KEY

[mcp_servers.eda-bridge-mcp]
command = "eda-bridge-mcp"
# 可选环境变量：VIVADO_ROOT、VIVADO_PATH

[mcp_servers.waveform-mcp-rs]
command = "waveform-mcp-rs"
# 可选环境变量：VIBE4FPGA_LLM、ANTHROPIC_API_KEY、ANTHROPIC_AUTH_TOKEN、ANTHROPIC_BASE_URL

# ... 每个 MCP 一段 [mcp_servers.<name>] ...
```

### 必备环境变量

Codex **不**像 Claude Code 那样会内联展开 `${env:...}`。API key 用以下任一方式传：

* 在拉起 Codex 的进程里设真实 shell 环境变量
  （PowerShell `$env:ANTHROPIC_API_KEY = "sk-..."`，然后再 `codex`），或者
* 在 TOML 里 inline：

  ```toml
  [mcp_servers.datasheet-mcp]
  command = "datasheet-mcp"
  env = { OPENAI_API_KEY = "sk-..." }
  ```

  （别带真 secret 提交这份文件。）

## 技能（Skills）

Codex **没有原生的 skill 格式**。这些 prompt 上下文是焙在 MCP 工具描述里的：Codex 跟 MCP 问 `list_tools` 时，每个工具的 `description` 字段就承载了 skill 的内容。所以你只要正常说就行：

> Generate a SystemVerilog testbench for this module, 1ns timescale, with
> assertions for reset recovery.

Codex 在自己的工具列表里看见 `verify-mcp.generate_testbench`，自动路由。每个 MCP 的 `skill.yaml` 里的 `codex_alias` 字段是个简写提示，可以在 prompt 里提一下（比如 "use s2r" 暗示走 `spec_to_rtl`）。

## 验证

```bash
codex --list-mcp
```

应该列出全部 8 个 MCP，状态都是 `connected`。

再跑一次端到端：

```bash
codex "scan the project in ./rtl and list modules"
```

预期 Codex 调 `fpga-project-mcp.scan_project`，返回模块图。

## 排错

| 现象 | 原因 | 修法 |
| --- | --- | --- |
| `command not found: fpga-project-mcp` | 入口不在 PATH | 在该 MCP 包目录 `uv tool install .`；确认 `~/.local/bin`（mac）或 `%USERPROFILE%\.local\bin`（win）在 PATH 里 |
| `mcp_server fpga-project-mcp: transport closed` | MCP 起来就崩 | 手动 `fpga-project-mcp` 看 stderr；最常见是缺 API key 环境变量 |
| Codex 没暴露出某个工具 | `list_tools` 里没有 | 确认 `skill.yaml` 和 `@mcp.tool()` 注册一致（CI 里 `skill_yaml_parity` 测试会卡这个） |
| Vivado / Quartus / Yosys 调用 `ProcessTimeoutError` | 外部工具慢或者根本没装 | 看该 MCP 的 README Environment 节；大部分支持 `VIVADO_ROOT`、`QUARTUS_SH`、`YOSYS_PATH` 这类环境变量 |
