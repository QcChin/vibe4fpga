# waveform-mcp-rs

Rust-native MCP server for VCD/FST waveform file analysis. Lower-latency
sibling of the Python [`waveform-mcp`](../waveform-mcp/); same four tools,
multi-level compression, no LLM calls.

## Overview

Parses IEEE-1364 VCD (and, in future, FST) waveform dumps and exposes
structured metadata over the MCP stdio transport. All analysis is pure
Rust — no subprocess, no network, no scratch files — so this MCP runs
anywhere `rustc` runs and pairs cleanly with host agents (Claude Code,
Codex CLI, OpenCode).

| Tool | What it does |
| --- | --- |
| `parse_waveform` | Parse a VCD/FST file; return format, duration, signals, detected clocks. |
| `extract_signal_events` | L1-compressed time-value events for signals in a time window (stable regions collapsed). |
| `get_signal_stats` | Transition count, first/last event time, clock detection, value distribution for one signal. |
| `summarize_waveform` | LLM-friendly structured summary: clocks, most-active signals, full signal list. |

An 8-entry LRU cache keyed on `file_path` avoids re-parsing large dumps
across repeated tool calls.

## Install

Prebuilt binaries (main path — see R2 in the risk register):

1. Download the appropriate archive from the
   [GitHub Releases](https://github.com/vibe4fpga/vibe4fpga/releases)
   page: `waveform-mcp-rs-windows-x86_64.zip` on Windows,
   `waveform-mcp-rs-<triple>.tar.gz` on macOS/Linux.
2. Extract and place `waveform-mcp-rs` (or `waveform-mcp-rs.exe`) on
   `PATH`.

From source (backup path, requires a Rust toolchain):

```bash
cargo install --path packages/mcp-servers/waveform-mcp-rs
# or, from inside the crate:
cd packages/mcp-servers/waveform-mcp-rs
cargo install --path .
```

Developers working inside the monorepo just use:

```bash
cd packages/mcp-servers/waveform-mcp-rs
cargo build --release                 # binary at target/release/waveform-mcp-rs
uv sync --extra dev                   # pytest + vibe4fpga-mcp-testkit
```

## Configure

### Claude Code — `%USERPROFILE%\.claude.json`

```json
{
  "mcpServers": {
    "waveform-rs": { "command": "waveform-mcp-rs" }
  }
}
```

### Codex CLI — `%USERPROFILE%\.codex\config.toml`

```toml
[mcp_servers.waveform-rs]
command = "waveform-mcp-rs"
```

### OpenCode — `%APPDATA%\opencode\config.json`

```json
{ "mcp": { "waveform-rs": { "type": "stdio", "command": ["waveform-mcp-rs"] } } }
```

There are no host skill surfaces generated for this MCP — `skill.yaml`
declares `skills: []` because the tools are pure parsing, not LLM
pipelines.

## Tools

All four tools above are already documented in the Overview table; see
`src/main.rs` `list_tools` for the authoritative JSON schemas. Quick
reference:

* **`parse_waveform`** — required: `file_path`.
* **`extract_signal_events`** — required: `file_path`, `signals` (array of
  fully-qualified names; a comma-separated string is also accepted).
  Optional: `time_start_ns` (default 0), `time_end_ns` (default end of
  simulation).
* **`get_signal_stats`** — required: `file_path`, `signal_name`.
* **`summarize_waveform`** — required: `file_path`. Optional:
  `max_signals` (default 50).

## Environment

None required. The binary is self-contained; it does not read any env
vars beyond `RUST_LOG` (tracing filter — writes to stderr, never stdout,
to keep the MCP JSON-RPC stream clean).

## Windows notes

* **MSVC build tools are required for a source build on Windows** —
  install "Visual Studio Build Tools" with the "Desktop development with
  C++" workload, or use `rustup default stable-x86_64-pc-windows-msvc`
  after the VS Build Tools are present. If MSVC is unavailable (ARM
  Windows, locked-down machines), use the prebuilt Release binary — see
  R2 in the plan.
* **File paths.** VCD paths passed to the tools should be absolute.
  Windows long paths (>260 chars) are handled by the OS when
  `LongPathsEnabled=1` is set; the Rust binary does not add the `\\?\`
  prefix itself.
* **Console encoding.** Unlike the Python siblings, this MCP does not
  write localized output to the console; all tool responses are JSON
  over stdio. No `chcp 65001` dance required.

## Troubleshooting

* `cargo: command not found` during source install — install Rust from
  <https://rustup.rs>.
* `linker cc not found` on macOS — install the Xcode command-line tools:
  `xcode-select --install`.
* `link.exe not found` or `note: the msvc targets depend on the msvc linker`
  on Windows — install Visual Studio Build Tools (see Windows notes).
* Tool returns `{"error": "Signal '…' not found"}` — use the full
  hierarchical path as reported by `parse_waveform` (e.g. `tb.dut.clk`,
  not just `clk`).
* Smoke tests print "Rust binary not built" and skip — run
  `cargo build --release && export PATH="$PWD/target/release:$PATH"` or
  `cargo install --path .` first, then rerun `uv run pytest`.
