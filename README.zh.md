# vibe4fpga

> 🌐 **中文** · [English](README.md)

8 个 [Model Context Protocol](https://modelcontextprotocol.io) 服务，为
**Claude Code**、**Codex CLI** 和 **OpenCode** 提供一个 FPGA 工程师真正
需要的工具集：项目扫描、规格转 RTL、代码评审、时序修复、Testbench 生成、
波形调试、示波器关联、Datasheet RAG，以及对 Vivado / Quartus / Yosys
的实时 EDA 桥接。

跑在 **Windows** 上，在 **macOS** 上开发。

## 快速上手

```bash
# 1. 没装 uv 的先装一个（uv tool install 需要 Python 3.11-3.12）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 从源码安装全部 MCP（7 个 Python + 1 个 Rust）
make install

# 3. 让 agent 宿主指向生成好的配置
#    见 configs/{claude-mcp-config.json, codex-config.toml, opencode-mcp-config.yaml}
#    或 docs/integration-*.md 里逐宿主的接入说明
```

## 你能拿到什么

### 8 个 MCP 服务

| MCP | 工具数 | 亮点 |
| --- | ----- | --- |
| **fpga-project-mcp** | 9 | 扫描 RTL 树 · 模块层级 · 信号搜索 · **spec→RTL** · **代码评审** · **时序修复** |
| **eda-bridge-mcp** | 5 | Vivado lint / 综合 · Icarus / Verilator 仿真 · 时序报告解析 |
| **waveform-mcp-rs** | 7 | Rust 原生 VCD/FST parser · AXI4 解码器 · RTL 源映射 · **waveform debug** 技能（内置 Anthropic HTTP 客户端） |
| **instrument-mcp** | 8 | pyvisa SCPI · 实时示波器抓波 · FFT · **示波器 vs 仿真对比**技能 |
| **datasheet-mcp** | 6 | LlamaIndex + Qdrant 在 datasheet / IP 文档上做 RAG |
| **quartus-mcp** | 8 | QSF 管理 · 编译 · TimeQuest · JTAG · **edition 自动识别** |
| **yosys-mcp** | 7 | Yosys 综合 · nextpnr PnR · icepack 比特流 · 形式化准备 |
| **verify-mcp** | 2 | **Testbench 生成** · **多阶段评分**（lint / sim / formal / synth / spec） |

**加粗**的工具是 LLM 驱动的技能（skill），以 Claude Code skill、OpenCode 命令、
Codex alias 三种形式同时暴露给 agent 宿主（每个 MCP 的 `skill.yaml` 是源头，
[`tools/gen-skills/`](tools/gen-skills/) 负责派生）。

### 2 个共享库

* [`vibe4fpga-llm-client`](packages/shared/llm-client/) —— 统一的流式适配器
  接口（`claude` / `gpt-4o` / `deepseek` / `gemini` / `ollama` / `rtlcoder`），
  靠环境变量选后端，SDK 按需懒加载，只装你用得到的。
* [`vibe4fpga-platform`](packages/shared/platform/) —— 跨平台 scratch
  路径（Windows 上不再被 `/tmp` 坑），tool 发现（认 PATHEXT 和厂商安装
  变量），异步 `run()` 自动给子进程注入 UTF-8 以避免中文 Windows 上的
  GBK 乱码。

## 目录结构

```
packages/
  shared/                      vibe4fpga-{llm-client, platform, mcp-testkit}
  mcp-servers/
    fpga-project-mcp/          spec2rtl · code_review · timing_fix
    waveform-mcp-rs/           waveform_debug · VCD/FST · AXI4 · RTL 映射（Rust）
    instrument-mcp/            instrument_analyze
    verify-mcp/                testbench_gen · verification
    eda-bridge-mcp/            Vivado / Verilator / Icarus
    quartus-mcp/               Intel Quartus Prime
    yosys-mcp/                 Yosys + nextpnr（开源链路）
    datasheet-mcp/             PDF RAG（向量化，不是聊天）
tools/gen-skills/              skill.yaml → 各宿主配置的生成器
configs/                       已生成、已提交的宿主配置片段
.claude/skills/                每个 skill 的 SKILL.md（已生成）
.opencode/commands/            每个 skill 的 command.md（已生成）
docs/                          架构 / 接入 / 系统准备文档
```

## 进一步阅读

* [`docs/architecture.md`](docs/architecture.md) —— MCP-first 设计决策
* [`docs/integration-claude-code.md`](docs/integration-claude-code.md) —— 配 Claude Code
* [`docs/integration-codex.md`](docs/integration-codex.md) —— 配 Codex CLI
* [`docs/integration-opencode.md`](docs/integration-opencode.md) —— 配 OpenCode
* [`docs/windows-setup.md`](docs/windows-setup.md) —— Vivado PATH、VISA 驱动、长路径、UTF-8
* [`docs/mac-dev-setup.md`](docs/mac-dev-setup.md) —— 在 Mac 上开发、瞄准 Windows 目标

## 状态

- 8 个 MCP 服务 **v0.3.0**（Python 3.11 – 3.12，Rust stable）
- Mac + Win CI 矩阵覆盖 10 个 Python 包 + 1 个 Rust crate
- 漂移检测：`tools/gen-skills/generate.py --check` 在宿主适配器与
  `skill.yaml` 不一致时让 CI 失败
- 硬编码路径检测：`rg '"/tmp' packages/` 必须返回零命中
- 每个 MCP 都跑 Tier 1 smoke（握手 + `list_tools`）；含核心进程内逻辑的
  那几个 MCP 跑 Tier 2 纯逻辑单测

## 许可证

MIT。
