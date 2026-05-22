# Integration — Claude Code

> 🌐 [中文](integration-claude-code.zh.md) · **English**

## Prerequisites

* **uv** 0.4+ (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
* **Python** 3.11 or 3.12 (3.13 is not yet supported by the `mcp` SDK)
* **Claude Code** ([install](https://claude.com/claude-code))
* Optional: **Rust stable** for `waveform-mcp-rs`; **Vivado / Quartus / Yosys / pyvisa** for the EDA-bridge / quartus / yosys / instrument MCPs

## Install the MCPs

From the repo root:

```bash
make install
```

Or per-MCP, from inside each package:

```bash
cd packages/mcp-servers/fpga-project-mcp
uv sync --all-extras
uv tool install .   # exposes `fpga-project-mcp` on your PATH
```

Repeat for the seven other MCPs you want active.

## Configure `~/.claude.json`

The generator ships a ready-to-paste snippet at
[`configs/claude-mcp-config.json`](../configs/claude-mcp-config.json).
Merge its `mcpServers` object into your host config:

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

On Windows the file lives at `%USERPROFILE%\.claude.json`; on macOS at
`~/.claude.json`. Environment-variable interpolation (`${env:...}`) works
on both.

## Skills

The repo ships a `.claude/skills/` directory with one SKILL.md per
LLM-backed tool. Claude Code auto-discovers these when you copy the
directory into a project that Claude Code can see (for example, clone this
repo or symlink `.claude/skills/` into your target project).

Available skills:

| Skill file | Backing MCP | Intent trigger |
| --- | --- | --- |
| `spec2rtl/SKILL.md` | fpga-project-mcp | "spec", "rtl", "verilog from english" |
| `code_review/SKILL.md` | fpga-project-mcp | "review", "lint", "cdc", "latch" |
| `timing_fix/SKILL.md` | fpga-project-mcp | "timing", "slack", "wns", "critical path" |
| `waveform_debug/SKILL.md` | waveform-mcp-rs | "waveform", "vcd", "glitch", "stall" |
| `instrument_analyze/SKILL.md` | instrument-mcp | "oscilloscope", "scope", "diff classify" |
| `testbench_gen/SKILL.md` | verify-mcp | "testbench", "tb", "sim driver" |
| `verification/SKILL.md` | verify-mcp | "verify", "score", "verification report" |

## Verify

Restart Claude Code, open a chat, and run `/mcp`:

```
mcp servers:
  fpga-project ✓ connected
  eda-bridge   ✓ connected
  waveform     ✓ connected
  ...
```

Then try a prompt matching one of the skill triggers, e.g.:

> Write an RTL module for a 4-bit saturating counter with asynchronous reset. Use spec2rtl.

Claude Code should call `fpga-project-mcp.spec_to_rtl` and stream back the
generated Verilog plus a self-check report.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `fpga-project ✗ failed to start` | MCP not on PATH | `uv tool install` from that package's dir, then restart Claude Code |
| `AuthError: ANTHROPIC_API_KEY is not set` | missing env var in the MCP's `env` block | add `"env": {"ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}"}` |
| Tool returns `ToolNotFoundError: vivado` | Vivado not on PATH or `VIVADO_ROOT` unset | set `VIVADO_ROOT` in the `env` block or add the Vivado `bin/` dir to PATH |
| Chinese / garbled characters in tool output | CN Windows GBK console | already handled by `vibe4fpga-platform`; see [`windows-setup.md`](windows-setup.md) if the issue persists |
| Skills don't appear in slash menu | `.claude/skills/` not in the project Claude Code is working on | either clone this repo into your workspace or copy `.claude/skills/` to your project root |
