# 架构

> 🌐 **中文** · [English](architecture.md)

vibe4fpga 是一个 **MCP-first** 的单一仓库：每个能力都藏在一个 stdio MCP
服务后面，编排（规划、工具选择、多轮对话、重试）由 agent 宿主（Claude
Code / Codex / OpenCode）承担。

```
┌──────────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│       Claude Code        │    │      Codex CLI      │    │      OpenCode       │
│  ~/.claude.json          │    │ ~/.codex/config.toml│    │ %APPDATA%\opencode  │
└────────────┬─────────────┘    └──────────┬──────────┘    └──────────┬──────────┘
             │                             │                          │
             └──────── stdio JSON-RPC ─────┴──────────── stdio JSON-RPC┘
                                 │
             ┌───────────────────┼──────────────────────────────┐
             │                   │                              │
             ▼                   ▼                              ▼
┌──────────────────────┐  ┌──────────────────────┐  ┌─────────────────────────┐
│ fpga-project-mcp  9  │  │ waveform-mcp-rs   7  │  │ eda-bridge-mcp       5  │
│  scan / review / ... │  │  vcd / debug / axi   │  │  vivado / verilator... │
└──────────┬───────────┘  └──────────┬───────────┘  └───────────┬────────────┘
           │                         │                          │
           └─────── shared libs ─────┴──────── shared libs ─────┘
                 │                                  │
          ┌──────┴──────────┐              ┌────────┴──────────┐
          │ llm-client      │              │ platform          │
          │  claude/gpt/    │              │  scratch_file,    │
          │  gemini/ollama/ │              │  find_tool, run   │
          │  rtlcoder       │              │  UTF-8 / \\?\ fix │
          └─────────────────┘              └───────────────────┘
```

（另外还有 5 个 MCP：`instrument-mcp`、`datasheet-mcp`、`quartus-mcp`、
`yosys-mcp`、`verify-mcp`。上图里的 Rust `waveform-mcp-rs` 在 v0.3.0
取代了原 Python `waveform-mcp`——工具暴露面相同（7 个），其中
`debug_waveform` 技能后端是内置的 Anthropic HTTP 客户端。）

## 为什么选 MCP-first

pivot 之前是 `VSCode 扩展 → LLM 路由 → 技能引擎 → MCP` 这套层。改成单纯 MCP，理由：

* **每个宿主自己就有 agent 循环。** Claude Code / Codex / OpenCode 都自带
  planner / executor / evaluator。我们原本的 `agent_loop` 是在重新造轮子。
* **一个后端，三种宿主。** MCP 就是这三个的交集：仓库出 8 个 MCP，用户
  挑任一宿主接进去都能用。
* **不再有路由进程要伺候。** 技能就是普通的 MCP 工具；
  `vibe4fpga-llm-client` 适配器库直接链接进需要 LLM 的 MCP，不走 HTTP。

## 共享库

### `vibe4fpga-llm-client`

6 个后端共用一个 `BaseAdapter` 契约的流式 chat-completion 适配器。厂商
SDK 走**可选 extras**：`[claude]`、`[openai]`、`[deepseek]`、`[gemini]`、
`[all]`。`adapter_from_env()` 根据 `VIBE4FPGA_LLM` + 对应的 API key 环境
变量挑后端。

### `vibe4fpga-platform`

跨平台原语，专治 Mac↔Windows 最常见的坑：

* `scratch_file(".vvp")` / `scratch_dir(prefix="")` —— 替代所有硬编码
  `/tmp/...` 和临时的 `tempfile.TemporaryDirectory()`。
* `find_tool(name, env_var="...", extra_paths=[...])` —— Windows 上认
  `PATHEXT` 和厂商安装提示（`VIVADO_ROOT`、`QUARTUS_SH` 等）。
* `async run(cmd, timeout=...)` —— 给子进程注入 `PYTHONIOENCODING=utf-8`
  + `PYTHONUTF8=1`，Windows 路径超过 240 字符时透明加 `\\?\` 前缀，
  超时统一升级成 `ProcessTimeoutError`。

### `vibe4fpga-mcp-testkit`

Dev-only：一行 fixture `stdio_server_spawn("<mcp-name>")` 给 Tier 1 smoke
测试用。不发布。

## 单个 MCP 结构

每个 MCP 都长一样：

```
packages/mcp-servers/<name>/
  pyproject.toml             对 3 个共享库走 path-deps；厂商 SDK 走 extras，
                             默认安装保持精简
  skill.yaml                 gen-skills 消费的唯一真相源
  src/<pkg>/
    server.py                FastMCP 入口 + @mcp.tool() 注册
    skills/<skill>/          每个 LLM 驱动工具的逻辑 + prompt
    skills/_llm.py           套在 llm-client 上的薄壳 + JSON 解析
  tests/
    test_smoke.py            Tier 1 —— 握手 + list_tools
    test_skill_yaml_parity.py  声明的 skill 必须对应真实工具
    test_*.py                Tier 2 —— 纯逻辑单测
  README.md                  7 段式模板
```

## 宿主适配器生成

`tools/gen-skills/generate.py` 读每个 `skill.yaml`，吐出：

* `.claude/skills/<id>/SKILL.md` —— 每个 skill 的 Claude Code 文件
* `.opencode/commands/<name>.md` —— 每个 skill 的 OpenCode 命令文件
* `configs/{claude-mcp-config.json, codex-config.toml, opencode-mcp-config.yaml}` ——
  整队伍的宿主配置片段

所有产物都已提交。CI 跑一个 `--check`，发现漂移立刻让构建失败——三个
宿主格式锁定在 `skill.yaml`。

## 测试策略

| 层 | 谁跑 | 断什么 |
| -- | ---- | ------ |
| 1. Smoke | 每个 MCP、两个 OS | stdio 握手成功；`list_tools` 返回预期集合 |
| 2. 纯逻辑单测 | 含进程内逻辑的 MCP（scanner、detector、scorer） | 确定性输入给确定性输出；不打 LLM、不走网络、不调外部工具 |
| 3. 工具条件 | `eda-bridge`、`quartus`、`yosys`、`instrument` | 仅当 CI runner 上有对应外部工具（`vivado`、`quartus_sh`、`yosys`、`pyvisa` 后端）时跑 |
| 4. LLM smoke（可选） | 用 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` 闸控 | 只断响应形状（不断内容）；只在宿主提供 key 时跑 |

CI 矩阵：`macos-latest` × `windows-latest` × （10 个 Python 包 + 1 个
Rust crate），扣掉 `quartus-mcp × macos` 那一格（macOS 上没原生 Quartus）。

## 风险登记

完整列表见 `/Users/naspter/.claude/plans/cheeky-kindling-bachman.md` §12；要点：

* **R7** —— `/tmp` 硬编码路径在 Windows 上崩。缓解：`platform.scratch_file`
  + CI 里 `rg '"/tmp' packages/` 闸门。
* **R5** —— 中文 Windows 上 GBK 控制台会污染子进程 stdout。缓解：
  `platform.run` 强制子进程走 UTF-8。
* **R1** —— Windows 260 字符路径限。缓解：`platform.long_path` 在需要时
  自动加 `\\?\` 前缀。
* **R10** —— `vibe4fpga-llm-client` 版本在 7 个 Python MCP 间错位。
  缓解：caret-pin + 所有 MCP 用一个 tag 同步发布。

## 回滚

`pre-pivot-archive` tag 保存了 pivot 之前的整棵树（VSCode 扩展 + LLM
路由 + collab-server + 技能 monolith）。分支 `archive/agent_loop` 单独
保留了 330 行的自主循环——以备某天非 agent 的 batch / CI 用例冒出来。
