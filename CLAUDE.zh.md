# CLAUDE.md

> 🌐 **中文** · [English](CLAUDE.md)

本文件为 Claude Code（claude.ai/code）在本仓库工作时提供向导。

## 仓库形态

MCP-first 单一仓库：8 个 MCP 服务（7 Python + 1 Rust）给 agent 宿主（Claude Code / Codex CLI / OpenCode）提供 FPGA 工作流相关的工具——项目扫描、spec→RTL、代码评审、时序修复、Testbench 生成、波形与示波器分析、Datasheet RAG，以及 EDA 桥接（Vivado / Quartus / Yosys / Verilator / Icarus）。v0.3.0 起 Rust 的 `waveform-mcp-rs` 吸收了原 Python `waveform-mcp` 整包：现在它一个人负责 VCD/FST 解析、AXI4 解码、RTL 源映射，以及 LLM 驱动的 `debug_waveform` 技能（内置 Anthropic HTTP 客户端）。另外还有 3 个共享 Python 库：

- `packages/shared/llm-client/` —— 流式适配器（`claude` / `gpt-4o` / `deepseek` / `gemini` / `ollama` / `rtlcoder`），通过 `VIBE4FPGA_LLM` + 对应的 API key 环境变量选择后端。
- `packages/shared/platform/` —— 跨平台 scratch 路径、tool 发现、`async run()`（强制给子进程注入 UTF-8 环境，Windows 上对超长路径加 `\\?\` 前缀）。
- `packages/shared/mcp-testkit/` —— `stdio_server_spawn("<mcp-name>")` 这个夹具用于 Tier-1 smoke 测试（dev-only，不发布）。

完整画面见 `docs/architecture.md`。

## 常用命令

未注明时一律在仓库根目录跑。

| 命令 | 做什么 |
| ---- | ------ |
| `make install` | 没装 `uv` 就装；对每个 Python 包跑一次 `uv sync --all-extras`。首次接入用。 |
| `make sync` | 改了依赖后对所有 Python 包跑 `uv sync`。 |
| `make test` | 每个 Python 包跑 `pytest -q`，再对 `waveform-mcp-rs` 跑 `cargo test --locked`。任一失败立刻停。 |
| `make gen-skills` | 根据每个 MCP 的 `skill.yaml` 重新生成 `.claude/skills/*/SKILL.md`、`.opencode/commands/*.md`、`configs/*`。 |
| `make gen-skills-check` | CI 漂移检查——任意生成产物过时即失败。 |
| `make dev-<mcp-name>` | 通过 stdio 启一个 MCP（debug 用，比如 `make dev-fpga-project-mcp`）。 |

每个包内（在 `packages/.../<pkg>` 目录下）能做的事：
- `uv sync --all-extras` —— 同步该包依赖。
- `uv run pytest --tb=short -q` —— 只跑该包的测试。
- `uv run pytest tests/test_scanner.py::test_foo` —— 跑单测。
- `uv tool install .` —— 把 console-script（比如 `fpga-project-mcp`）注入 PATH，宿主配置就能 spawn 它了。

Rust：`cd packages/mcp-servers/waveform-mcp-rs && cargo test --locked`。

每个 `pyproject.toml` 都配了 Ruff（`select = ["E", "F", "I"]`），想 lint 在包目录里跑 `uv run ruff check .`。

## Python 版本

所有 Python 包瞄准 **3.11–3.12**（`requires-python = ">=3.11,<3.13"`）。`mcp` SDK 还不支持 3.13。CI 在 `macos-latest` 和 `windows-latest` 上都用 3.12。

## 单个 MCP 的结构（新增 MCP 照这个抄）

```
packages/mcp-servers/<name>/
  pyproject.toml          对 3 个共享库走 path-deps；厂商 SDK 走 [extras]
  skill.yaml              gen-skills 唯一的真相源
  src/<pkg>/
    server.py             FastMCP 入口 + @mcp.tool() 注册
    skills/<skill>/       每个 LLM 驱动工具的逻辑 + prompt
    skills/_llm.py        套在 llm-client 外的薄壳，包含 JSON 解析
  tests/
    test_smoke.py                Tier 1 —— 握手 + list_tools
    test_skill_yaml_parity.py    声明的 skill 必须对应真实工具
    test_*.py                    Tier 2 —— 纯逻辑单测
```

包间依赖是 path-editable（`[tool.uv.sources]` → `{ path = "../../shared/...", editable = true }`）。新增 MCP 就把这 3 行抄过去。

## skill.yaml 是真相源

`tools/gen-skills/generate.py` 读每个 `packages/mcp-servers/*/skill.yaml`，写出：
- `.claude/skills/<id>/SKILL.md`（Claude Code 格式）
- `.opencode/commands/<name>.md`（OpenCode 格式）
- `configs/claude-mcp-config.json`、`configs/codex-config.toml`、`configs/opencode-mcp-config.yaml`（整队伍的宿主片段）

**永远不要手改生成出来的文件** —— 改 `skill.yaml` 然后 `make gen-skills`。生成器只看 YAML（不 import MCP），所以刚拉下仓库还没装东西也能跑；每个 MCP 自己的 `test_skill_yaml_parity.py` 负责断言声明 vs `list_tools()` 实际产出。CI 跑 `--check`，漂移就失败。

## 跨平台规则（CI 强制）

1. **不允许 `/tmp` 硬编码。** `grep -RIn '"/tmp' packages/mcp-servers packages/shared` 必须零命中——用 `vibe4fpga_platform.scratch_file(suffix)` / `scratch_dir(prefix)`。`no-tmp-hardcode` CI job 否则会让构建失败。
2. **所有 subprocess 调用都走 `vibe4fpga_platform.run()`。** 它给子进程注入 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`（防止中文 Windows 上 GBK 乱码），Windows 路径超过 240 字符时加 `\\?\` 前缀，超时统一升级成 `ProcessTimeoutError`。
3. **Tool 发现走 `find_tool` / `require_tool`。** Windows 上认 `PATHEXT`，认厂商安装变量（`VIVADO_ROOT`、`QUARTUS_SH` 等）。可执行文件名别硬编码。
4. **Windows 是 Tier-1 目标。** 开发常在 macOS，但 CI 跑全矩阵：`macos-latest` × `windows-latest`（除了 `quartus-mcp × macOS` 这一格）。

## 测试分层

| 层 | 范围 |
| -- | ---- |
| 1. Smoke | 每个 MCP：stdio 握手 + `list_tools` 返回预期集合。 |
| 2. 纯逻辑单测 | 进程内有可观逻辑的 MCP（scanner、detector、scorer）。确定性，不打 LLM、不走网络、不调外部工具。 |
| 3. 工具条件 | `eda-bridge`、`quartus`、`yosys`、`instrument` —— 外部工具不在 runner 上就 skip。 |
| 4. LLM smoke | 用 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 闸控；只断响应形状，不断内容。 |

`pytest` 退出码 5（没收集到测试）在 bring-up 阶段 CI 接受—— 但别让它掩盖了你本该加但没加的测试文件。

## 发布

整队伍单 tag 发布：所有 9 个已发布的包（`mcp-testkit` 是 dev-only） + Rust crate 一起放出去。改完版本号，`make gen-skills-check && make test`，然后 `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`。流程文件 `.github/workflows/release.yml` —— 走 PyPI trusted publishing，通过 GitHub `release` 环境授权；不用维护 API token。每个新包首次发布前在 PyPI 注册 pending publisher 的步骤见 `docs/releasing.md`。

包名怪癖：7 个起源期的 Python MCP 用裸名发布（`fpga-project-mcp`、…）；只有 `verify-mcp` 发布名是 `vibe4fpga-verify-mcp`。不要"纠正"它——会把已经 `uv tool install` 的用户搞坏。
