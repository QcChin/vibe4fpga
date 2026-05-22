# Architecture

> 🌐 [中文](architecture.zh.md) · **English**

vibe4fpga is an **MCP-first** monorepo: each capability lives behind a
stdio MCP server, and the agent host (Claude Code / Codex / OpenCode) is
responsible for orchestration — planning, tool selection, multi-turn
conversation, retry loops.

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

(Plus 5 more MCPs: `instrument-mcp`, `datasheet-mcp`, `quartus-mcp`,
`yosys-mcp`, `verify-mcp`. The unified Rust `waveform-mcp-rs` shown above
replaced the former Python `waveform-mcp` in v0.3.0 — same 7-tool surface,
with a built-in Anthropic HTTP client backing the `debug_waveform` skill.)

## Why MCP-first

The pivot replaced a `VSCode extension → LLM Router → Skill Engine → MCP`
stack with plain MCPs. Rationale:

* **Every host already runs an agent loop.** Claude Code / Codex / OpenCode
  each have built-in planner/executor/evaluator. Our old `agent_loop` was
  reinventing that wheel.
* **One backend, three hosts.** MCP is the intersection: we ship eight
  servers, users pick any host.
* **No router process to babysit.** Skills are regular MCP tools; the
  adapter library (`vibe4fpga-llm-client`) is linked into each MCP that
  needs an LLM, not accessed over HTTP.

## Shared libraries

### `vibe4fpga-llm-client`

Streaming chat-completion adapter for 6 backends behind one `BaseAdapter`
contract. Vendor SDKs are **optional extras**: `[claude]`, `[openai]`,
`[deepseek]`, `[gemini]`, `[all]`. `adapter_from_env()` picks the backend
from `VIBE4FPGA_LLM` + the matching API key env var.

### `vibe4fpga-platform`

Cross-platform primitives that eliminate the most common Mac↔Windows
rough edges:

* `scratch_file(".vvp")` / `scratch_dir(prefix="")` — replaces every
  hardcoded `/tmp/...` and every ad-hoc `tempfile.TemporaryDirectory()`.
* `find_tool(name, env_var="...", extra_paths=[...])` — handles Windows
  `PATHEXT` + vendor install hints (`VIVADO_ROOT`, `QUARTUS_SH`, etc.).
* `async run(cmd, timeout=...)` — forces `PYTHONIOENCODING=utf-8` +
  `PYTHONUTF8=1` in the child env, transparently adds the `\\?\` prefix
  on Windows paths over 240 chars, and surfaces timeouts as
  `ProcessTimeoutError`.

### `vibe4fpga-mcp-testkit`

Dev-only: one-liner fixture `stdio_server_spawn("<mcp-name>")` for Tier 1
smoke tests. Not published.

## Per-MCP structure

Every MCP follows the same shape:

```
packages/mcp-servers/<name>/
  pyproject.toml             path-deps on the 3 shared libs; extras for
                             vendor SDKs so default install stays lean
  skill.yaml                 single source of truth consumed by gen-skills
  src/<pkg>/
    server.py                FastMCP entrypoint + @mcp.tool() registrations
    skills/<skill>/          logic + prompts for each LLM-backed tool
    skills/_llm.py           tiny wrapper around llm-client + JSON parsing
  tests/
    test_smoke.py            Tier 1 — handshake + list_tools
    test_skill_yaml_parity.py  asserts declared skills exist as real tools
    test_*.py                Tier 2 — pure-logic unit tests
  README.md                  7-section template
```

## Host-adapter generation

`tools/gen-skills/generate.py` reads every `skill.yaml` and produces:

* `.claude/skills/<id>/SKILL.md` — per-skill Claude Code skill files
* `.opencode/commands/<name>.md` — per-skill OpenCode command files
* `configs/{claude-mcp-config.json, codex-config.toml, opencode-mcp-config.yaml}` —
  whole-fleet host-config snippets

All outputs are committed. A CI job runs `--check` and fails the build on
any drift, keeping the three host formats locked to `skill.yaml`.

## Testing strategy

| Tier | Who runs it | What it asserts |
| --- | --- | --- |
| 1. Smoke | every MCP, on both OSes | stdio handshake succeeds; `list_tools` returns the expected set |
| 2. Pure-logic unit | MCPs with in-process logic (scanner, detector, scorer) | deterministic inputs give deterministic outputs; no LLM, no network, no external tools |
| 3. Tool-conditional | `eda-bridge`, `quartus`, `yosys`, `instrument` | run only when the external tool (`vivado`, `quartus_sh`, `yosys`, `pyvisa` backend) is present on the CI runner |
| 4. LLM smoke (optional) | env-gated on `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | asserts response shape (not content); runs only when a host-provided key is available |

CI matrix: `macos-latest` × `windows-latest` × (11 Python packages + 1 Rust
crate), minus the `quartus-mcp × macos` cell (no native macOS Quartus).

## Risk register

See `/Users/naspter/.claude/plans/cheeky-kindling-bachman.md` §12 for the
full table; highlights:

* **R7** — `/tmp` hardcoded paths break Windows. Mitigation: `platform.scratch_file`
  + `rg '"/tmp' packages/` grep gate in CI.
* **R5** — CN Windows GBK console corrupts subprocess stdout. Mitigation:
  `platform.run` forces UTF-8 in the child env.
* **R1** — Windows 260-char path limit. Mitigation: `platform.long_path`
  applies `\\?\` prefix automatically when needed.
* **R10** — `vibe4fpga-llm-client` version skew across 7 Python MCPs. Mitigation:
  caret-pin + release all MCPs together from a single tag.

## Rollback

`pre-pivot-archive` tag preserves the pre-pivot tree (VSCode extension +
LLM router + collab-server + skills monolith). Branch `archive/agent_loop`
preserves the 330-line autonomous loop separately in case a non-agent
batch/CI use case emerges.
