# Integration — Codex CLI

> 🌐 [中文](integration-codex.zh.md) · **English**

## Prerequisites

* **uv** 0.4+
* **Python** 3.11 or 3.12
* **Codex CLI** (`npm install -g @openai/codex` or similar — check the official docs)
* Optional: **Rust stable** for `waveform-mcp-rs`; **Vivado / Quartus / Yosys / pyvisa** for the EDA-bridge / quartus / yosys / instrument MCPs

## Install the MCPs

Same as Claude Code:

```bash
make install
```

Or per-MCP: `uv tool install .` from each package directory to put its
entry point on your PATH.

## Configure `~/.codex/config.toml`

The generator writes a ready-to-paste snippet to
[`configs/codex-config.toml`](../configs/codex-config.toml). Append it to
your Codex config (Windows path: `%USERPROFILE%\.codex\config.toml`):

```toml
[mcp_servers.fpga-project-mcp]
command = "fpga-project-mcp"
# Optional env vars: VIBE4FPGA_LLM, ANTHROPIC_API_KEY, OPENAI_API_KEY

[mcp_servers.eda-bridge-mcp]
command = "eda-bridge-mcp"
# Optional env vars: VIVADO_ROOT, VIVADO_PATH

[mcp_servers.waveform-mcp-rs]
command = "waveform-mcp-rs"
# Optional env vars: VIBE4FPGA_LLM, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL

# ... one [mcp_servers.<name>] section per MCP ...
```

### Required environment variables

Codex doesn't expand `${env:...}` in-line the way Claude Code does. Pass
API keys either:

* As real shell environment variables in the process that launches Codex
  (PowerShell `$env:ANTHROPIC_API_KEY = "sk-..."` before `codex`), or
* Inline in the TOML:

  ```toml
  [mcp_servers.datasheet-mcp]
  command = "datasheet-mcp"
  env = { OPENAI_API_KEY = "sk-..." }
  ```

  (Don't commit this file with live secrets.)

## Skills

Codex **does not have a native skills format**. Instead, the same
information is baked into the MCP tool descriptions: when Codex asks the
MCP `list_tools`, each tool's `description` field carries the prompt
context the skill implements. So you invoke skills by simply asking
naturally:

> Generate a SystemVerilog testbench for this module, 1ns timescale, with
> assertions for reset recovery.

Codex sees `verify-mcp.generate_testbench` in its tool list and routes.
The `codex_alias` field in each MCP's `skill.yaml` is a shorthand hint
you can mention in prompts (e.g. "use s2r" to nudge toward
`spec_to_rtl`).

## Verify

```bash
codex --list-mcp
```

Should enumerate all 8 MCPs with status `connected`.

Then trigger a quick round-trip:

```bash
codex "scan the project in ./rtl and list modules"
```

Expect Codex to call `fpga-project-mcp.scan_project` and return the
module graph.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `command not found: fpga-project-mcp` | entry point not on PATH | `uv tool install .` from the MCP package; confirm `~/.local/bin` (mac) or `%USERPROFILE%\.local\bin` (win) is on PATH |
| `mcp_server fpga-project-mcp: transport closed` | MCP crashed on startup | run `fpga-project-mcp` by hand and look at stderr; most common cause is missing API key env var |
| Codex doesn't surface a tool | the tool wasn't listed in `list_tools` | confirm `skill.yaml` and the `@mcp.tool()` registration are in sync (CI's `skill_yaml_parity` test catches this) |
| `ProcessTimeoutError` on a Vivado / Quartus / Yosys call | external tool is slow or missing | check the tool's README Environment section; most accept env-var overrides like `VIVADO_ROOT`, `QUARTUS_SH`, `YOSYS_PATH` |
