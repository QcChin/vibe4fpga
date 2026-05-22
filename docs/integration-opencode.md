# Integration — OpenCode

> 🌐 [中文](integration-opencode.zh.md) · **English**

## Prerequisites

* **uv** 0.4+
* **Python** 3.11 or 3.12
* **OpenCode** ([install](https://opencode.ai))
* Optional: **Rust stable** for `waveform-mcp-rs`; **Vivado / Quartus / Yosys / pyvisa** for the EDA-bridge / quartus / yosys / instrument MCPs

## Install the MCPs

```bash
make install
```

Or per-MCP: `uv tool install .` from each package.

## Configure OpenCode

OpenCode reads MCP config from `%APPDATA%\opencode\config.json` (Windows)
or `~/.config/opencode/config.json` (macOS/Linux). You can also scope the
config per-project via `.opencode/opencode.json` at the project root.

Generator output at [`configs/opencode-mcp-config.yaml`](../configs/opencode-mcp-config.yaml) — paste
the `mcp:` block into your JSON config (translating YAML → JSON) or keep
it as YAML if your OpenCode version supports that:

```json
{
  "mcp": {
    "fpga-project-mcp": { "type": "stdio", "command": ["fpga-project-mcp"] },
    "eda-bridge-mcp":   { "type": "stdio", "command": ["eda-bridge-mcp"] },
    "waveform-mcp-rs":  { "type": "stdio", "command": ["waveform-mcp-rs"] },
    "instrument-mcp":   { "type": "stdio", "command": ["instrument-mcp"] },
    "datasheet-mcp":    { "type": "stdio", "command": ["datasheet-mcp"],
                          "env": { "OPENAI_API_KEY": "${OPENAI_API_KEY}" } },
    "quartus-mcp":      { "type": "stdio", "command": ["quartus-mcp"],
                          "env": { "QUARTUS_ROOTDIR": "C:\\intelFPGA_lite\\23.1\\quartus" } },
    "yosys-mcp":        { "type": "stdio", "command": ["yosys-mcp"] },
    "verify-mcp":       { "type": "stdio", "command": ["verify-mcp"] }
  }
}
```

OpenCode expands `${FOO}` from process env vars, so exporting
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in your shell before launching
`opencode` is the cleanest way to feed credentials.

## Commands (skills)

The repo ships `.opencode/commands/` with one `command.md` per
LLM-backed tool. Copy the directory into your project root (or clone this
repo directly), and OpenCode will surface each skill as a slash command:

```
/spec2rtl         → fpga-project-mcp.spec_to_rtl
/review           → fpga-project-mcp.review_rtl
/timing           → fpga-project-mcp.suggest_timing_fix
/debug-wave       → waveform-mcp-rs.debug_waveform
/analyze-diff     → instrument-mcp.analyze_instrument_diff
/gen-tb           → verify-mcp.generate_testbench
/score-verify     → verify-mcp.score_verification
```

Each command file names the backing MCP + tool, env vars it needs, and
a brief description — OpenCode uses all of that when the user hits tab.

## Verify

```bash
opencode
```

Then:

```
/mcp list
```

Should print 8 servers, all `connected`. Try:

```
/spec2rtl Write a 4-bit saturating counter with synchronous reset.
```

Expect OpenCode to delegate to `fpga-project-mcp.spec_to_rtl` and return
the RTL + self-check.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Slash command not listed | `.opencode/commands/` not in the project OpenCode is running in | clone this repo in your workspace, or symlink `.opencode/commands/` into your project root |
| `mcp.fpga-project-mcp: exited immediately` | MCP crashed at startup | run the binary directly (`fpga-project-mcp`) and inspect stderr; most common: missing Python deps or wrong Python version |
| Tool output garbled (Windows) | GBK console, non-UTF8 stdout | already handled by `vibe4fpga-platform`; if it persists, `set PYTHONUTF8=1` before launching OpenCode |
| `AuthError: ANTHROPIC_API_KEY is not set` | env var not exported before `opencode` started | `$env:ANTHROPIC_API_KEY = "sk-..."` then relaunch opencode |
