# vibe4fpga

Nine [Model Context Protocol](https://modelcontextprotocol.io) servers that
give **Claude Code**, **Codex CLI**, and **OpenCode** the tools an FPGA
engineer actually needs: project scanning, spec-to-RTL generation, code
review, timing fixing, testbench synthesis, waveform debugging, oscilloscope
correlation, datasheet RAG, and live EDA (Vivado / Quartus / Yosys) bridging.

Runs on **Windows**; developed on **macOS**.

## Quick start

```bash
# 1. install uv if you don't have it (uv tool install needs Python 3.11-3.12)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. install all MCPs from source (9 Python + 1 Rust)
make install

# 3. point your agent host at the generated config
#    see configs/{claude-mcp-config.json, codex-config.toml, opencode-mcp-config.yaml}
#    or the per-host guides in docs/integration-*.md
```

## What you get

### Nine MCP servers

| MCP | Tools | Highlights |
| --- | ----- | --- |
| **fpga-project-mcp** | 9 | scan RTL tree · module hierarchy · signal search · **spec→RTL** · **code review** · **timing fix** |
| **eda-bridge-mcp** | 5 | Vivado lint / synth · Icarus / Verilator sim · timing report parsing |
| **waveform-mcp** | 6 | VCD/FST parsing · 3-level compression · AXI4 decoder · **waveform debug** skill |
| **waveform-mcp-rs** | 4 | Rust implementation · lower latency, smaller binary |
| **instrument-mcp** | 8 | pyvisa SCPI · live scope capture · FFT · **oscilloscope-vs-sim diff** skill |
| **datasheet-mcp** | 6 | LlamaIndex + Qdrant RAG over datasheets / IP docs |
| **quartus-mcp** | 8 | QSF management · compile · TimeQuest · JTAG · **edition detection** |
| **yosys-mcp** | 7 | Yosys synth · nextpnr PnR · icepack bitstream · formal prep |
| **verify-mcp** | 2 | **testbench generation** · **multi-stage scoring** (lint/sim/formal/synth/spec) |

**Bold** tools are LLM-backed skills surfaced to your agent host as Claude
Code skills, OpenCode commands, and Codex aliases (see `skill.yaml` in each
MCP, and [`tools/gen-skills/`](tools/gen-skills/) for how they're derived).

### Two shared libraries

* [`vibe4fpga-llm-client`](packages/shared/llm-client/) — one streaming
  adapter API (`claude` / `gpt-4o` / `deepseek` / `gemini` / `ollama` /
  `rtlcoder`), env-driven backend selection, lazy SDK imports so you only
  install what you use.
* [`vibe4fpga-platform`](packages/shared/platform/) — cross-platform
  scratch paths (no more `/tmp` breakage on Windows), tool discovery with
  PATHEXT + env-var overrides, async `run()` that forces UTF-8 on CN Windows.

## Layout

```
packages/
  shared/                      vibe4fpga-{llm-client, platform, mcp-testkit}
  mcp-servers/
    fpga-project-mcp/          spec2rtl · code_review · timing_fix
    waveform-mcp/              waveform_debug
    instrument-mcp/            instrument_analyze
    verify-mcp/                testbench_gen · verification
    eda-bridge-mcp/            Vivado / Verilator / Icarus
    quartus-mcp/               Intel Quartus Prime
    yosys-mcp/                 Yosys + nextpnr (open-source)
    datasheet-mcp/             PDF RAG (embeddings, not chat)
    waveform-mcp-rs/           Rust alternate
tools/gen-skills/              generator: skill.yaml → host configs
configs/                       committed host-config snippets (auto-generated)
.claude/skills/                committed per-skill SKILL.md files
.opencode/commands/            committed per-skill command.md files
docs/                          architecture, integration, setup guides
```

## Deeper reading

* [`docs/architecture.md`](docs/architecture.md) — MCP-first design decisions
* [`docs/integration-claude-code.md`](docs/integration-claude-code.md) — configure Claude Code
* [`docs/integration-codex.md`](docs/integration-codex.md) — configure Codex CLI
* [`docs/integration-opencode.md`](docs/integration-opencode.md) — configure OpenCode
* [`docs/windows-setup.md`](docs/windows-setup.md) — Vivado PATH, VISA drivers, long paths, UTF-8
* [`docs/mac-dev-setup.md`](docs/mac-dev-setup.md) — develop on Mac for a Windows target

## Status

- 9 MCP servers at **v0.2.0** (Python 3.11 – 3.12, Rust stable)
- Mac + Win CI matrix across 11 Python packages + 1 Rust crate
- Drift gate: `tools/gen-skills/generate.py --check` fails the build when
  host adapters are out of sync with `skill.yaml`
- Hardcoded-path gate: `rg '"/tmp' packages/` must return zero hits
- Tier 1 smoke (handshake + `list_tools`) on every MCP; Tier 2 pure-logic
  unit tests on the five MCPs that have non-trivial in-process logic

## License

MIT.
