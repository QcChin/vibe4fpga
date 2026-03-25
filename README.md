# vibe4fpga

AI-assisted FPGA development IDE — brings LLM-powered code generation, waveform analysis, timing repair, and autonomous design loops to your Verilog/SystemVerilog workflow.

## Overview

vibe4fpga is a monorepo that wires together a VSCode extension, an LLM router, a set of MCP servers, and a skill engine into a complete AI co-pilot for FPGA engineers. It supports both cloud models (Claude) and local fine-tuned models (RTLCoder/CodeV via Ollama), and works with Xilinx Vivado, Intel Quartus Prime, and the open-source Yosys/nextpnr toolchain.

```
VSCode Extension
       │  SSE / HTTP
       ▼
  LLM Router  (:8765)
       │
  ┌────┴──────────────────────────────────────────────┐
  │                   Skill Engine                    │
  │  Spec2RTL · CodeReview · WaveformDebug · TimingFix│
  │  TestbenchGen · Verification · InstrumentAnalyze  │
  │  AgentLoop                                        │
  └────┬──────────────────────────────────────────────┘
       │  MCP Protocol
  ┌────┴──────────────────────────────────┐
  │           MCP Servers                 │
  │  fpga-project · eda-bridge · waveform │
  │  instrument · datasheet               │
  │  quartus · yosys                      │
  └────┬──────────────────────────────────┘
       │
  EDA Tools / Hardware
  Vivado · Quartus · Yosys · nextpnr · Oscilloscope
```

---

## Features

### Phase 1 — RTL Generation & Review
- **Spec2RTL** — 5-stage pipeline: spec parsing → ambiguity detection → RAG context injection → RTL generation → self-check repair loop
- **CodeReview** — static analysis against 10 FPGA pitfall categories (CDC, reset strategy, FSM encoding, resource inference …)
- **fpga-project-mcp** — multi-file project scanner, module hierarchy builder, signal cross-reference search

### Phase 2 — Waveform Debugging & Verification
- **WaveformDebug** — 5 parallel detectors: glitch, X/Z state, CDC violation, AXI protocol, handshake timeout
- **TimingFix** — reads Vivado timing reports, suggests pipelining / multicycle path / logic restructure fixes
- **TestbenchGen** — generates coverage-driven testbenches with SVA assertions and functional covergroups
- **Verification Pipeline** — 5-layer flow: Lint → Simulation → Formal (SymbiYosys) → Synthesis/Timing → Spec compliance; score 0–100
- **waveform-mcp** — VCD/FST parser with 3-level compression (L1 edge-preserving, L2 semantic, L3 query-pruning) targeting a 4000-token LLM budget; AXI4 5-channel decoder
- **datasheet-mcp** — LlamaIndex + Qdrant RAG for datasheets, protocol specs, and IP interface docs

### Phase 3 — Measurement Correlation
- **InstrumentAnalyze** — cross-correlates oscilloscope captures with simulation VCDs, classifies differences into 7 categories (expected / suspicious / anomalous), and performs LLM root-cause attribution
- **instrument-mcp** — multi-vendor CSV readers (Rigol, generic), live SCPI capture via pyvisa, FFT spectral analysis

### Phase 4 — Autonomous Agent & Open Toolchain
- **AgentLoop** — LLM planner decomposes goals into steps, executes them in dependency order with concurrent scheduling and multi-round re-planning on failure
- **RTLCoder adapter** — Ollama-hosted fine-tuned HDL model with instruction-format wrapping
- **quartus-mcp** — QSF management, full/incremental compilation, TimeQuest analysis, USB-Blaster JTAG programming
- **yosys-mcp** — Yosys synthesis for iCE40/ECP5/generic, nextpnr place & route, icepack bitstream generation
- **collab-server** — JWT/API-key auth, RBAC (viewer/contributor/admin), per-team/project/user RAG namespaces, remote agent run API

---

## Repository Structure

```
vibe4fpga/
├── packages/
│   ├── vscode-extension/          # TypeScript VSCode extension
│   ├── llm-router/                # FastAPI LLM router (Claude, Ollama, RTLCoder)
│   ├── skills/                    # Python skill engine
│   │   └── src/skills/
│   │       ├── spec2rtl/
│   │       ├── code_review/
│   │       ├── waveform_debug/
│   │       ├── timing_fix/
│   │       ├── testbench_gen/
│   │       ├── verification/
│   │       ├── instrument_analyze/
│   │       └── agent_loop/
│   ├── mcp-servers/
│   │   ├── fpga-project-mcp/
│   │   ├── eda-bridge-mcp/
│   │   ├── waveform-mcp/
│   │   ├── instrument-mcp/
│   │   ├── datasheet-mcp/
│   │   ├── quartus-mcp/
│   │   └── yosys-mcp/
│   └── collab-server/             # Team collaboration & shared RAG
├── Makefile
└── package.json
```

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Node.js | ≥ 20 | VSCode extension build |
| Python | ≥ 3.11 | All Python packages |
| [uv](https://github.com/astral-sh/uv) | latest | Python dependency management |
| Vivado / Quartus | any | Synthesis & implementation (optional) |
| Yosys + nextpnr | latest | Open-source toolchain (optional) |
| Verilator / Verible | any | Lint (optional) |
| Icarus Verilog | any | Simulation (optional) |
| pyvisa + pyvisa-py | any | Live instrument capture (optional) |
| Ollama | any | Local LLM / RTLCoder (optional) |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/QcChin/vibe4fpga.git
cd vibe4fpga
make install
```

### 2. Configure the LLM Router

```bash
cp packages/llm-router/.env.example packages/llm-router/.env
# Edit .env — at minimum set ANTHROPIC_API_KEY
```

```env
ANTHROPIC_API_KEY=sk-ant-...
OLLAMA_BASE_URL=http://localhost:11434   # optional, for local models
```

### 3. Start the LLM Router

```bash
make dev-router
# Listening on http://localhost:8765
```

### 4. Start the MCP servers you need

```bash
make dev-fpga-project-mcp    # project indexing
make dev-eda-bridge-mcp      # Vivado/Verilator bridge
make dev-waveform-mcp        # VCD waveform analysis
make dev-instrument-mcp      # oscilloscope integration
make dev-datasheet-mcp       # RAG knowledge base
make dev-quartus-mcp         # Intel Quartus (needs quartus_sh in PATH)
make dev-yosys-mcp           # Yosys/nextpnr open-source toolchain
```

Or start everything at once:

```bash
make dev-all
```

### 5. Install the VSCode Extension

```bash
cd packages/vscode-extension
npm run build
# Then install the generated .vsix or open the folder in VSCode with F5
```

---

## LLM Router API

The router runs on `http://localhost:8765`.

### Chat

```bash
# Non-streaming
curl -X POST http://localhost:8765/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Write a 4-bit counter"}], "model": "claude"}'

# Streaming (SSE)
curl -N http://localhost:8765/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Explain clock domain crossing"}], "model": "claude"}'
```

### Skills

| Endpoint | Description |
|---|---|
| `POST /skill/spec2rtl` | Generate RTL from natural language spec |
| `POST /skill/code_review` | Review RTL code for FPGA pitfalls |
| `POST /skill/waveform_debug` | Analyze VCD waveform for anomalies |
| `POST /skill/timing_fix` | Suggest fixes for timing violations |
| `POST /skill/testbench_gen` | Generate SystemVerilog testbench |
| `POST /skill/verify` | Run 5-layer verification pipeline |
| `POST /skill/instrument_analyze` | Correlate oscilloscope vs. simulation |
| `POST /skill/agent_loop` | Run autonomous multi-round design loop |

#### Example: Spec2RTL

```bash
curl -X POST http://localhost:8765/skill/spec2rtl \
  -H 'Content-Type: application/json' \
  -d '{
    "spec": "Design a UART transmitter: 115200 baud, 8N1, active-low reset, AXI-Stream input interface",
    "model": "claude"
  }'
```

#### Example: Agent Loop

```bash
curl -X POST http://localhost:8765/skill/agent_loop \
  -H 'Content-Type: application/json' \
  -d '{
    "goal": "Generate, lint, and simulate a SPI master controller",
    "context": {"toolchain": "yosys"},
    "model": "claude",
    "max_rounds": 3
  }'
```

---

## Supported LLM Backends

| Key | Model | Notes |
|---|---|---|
| `claude` | claude-sonnet-4-6 | Default, best for code generation |
| `claude-opus` | claude-opus-4-6 | Complex reasoning tasks |
| `claude-haiku` | claude-haiku-4-5 | Fast, low-cost |
| `ollama` | configurable | Offline / private deployment |
| `rtlcoder` | RTLCoder (Ollama) | Fine-tuned HDL generation |
| `rtlcoder-7b` | RTLCoder 7B | Smaller, faster |
| `codev` | CodeV (Ollama) | Alternative fine-tuned model |

Set `OLLAMA_MODEL` in `.env` to configure the default Ollama model.

---

## Team Collaboration (collab-server)

The collab-server provides a shared RAG knowledge base and remote agent execution API for teams.

```bash
cp packages/collab-server/.env.example packages/collab-server/.env
make dev-collab-server
# Listening on http://localhost:8766
```

### Authentication

```bash
# Exchange API key for JWT
curl -X POST http://localhost:8766/token \
  -d '{"api_key": "your-api-key"}'

# Use JWT for subsequent requests
TOKEN="<jwt-from-above>"
```

### Knowledge Base

```bash
# Index a document
curl -X POST http://localhost:8766/knowledge/index \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"content": "...", "doc_id": "uart-spec-v2", "scope": "project", "scope_id": "uart-project"}'

# Search
curl "http://localhost:8766/knowledge/search?query=AXI+handshake+timeout&scope=global" \
  -H "Authorization: Bearer $TOKEN"
```

### Remote Agent Run

```bash
curl -X POST http://localhost:8766/agent/run \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"goal": "Design and verify a PWM controller", "model": "claude"}'

# Poll result
curl http://localhost:8766/agent/<run_id> \
  -H "Authorization: Bearer $TOKEN"
```

---

## Open-Source Toolchain (Yosys + nextpnr)

Full iCE40 flow example:

```python
import httpx, asyncio

async def main():
    base = "http://localhost:8765"

    # 1. Synthesize for iCE40
    r = await httpx.AsyncClient().post(f"{base}/tool/yosys/synthesize_ice40", json={
        "source_files": ["/path/to/top.v"],
        "top_module": "top",
    })
    netlist = r.json()["output_json"]   # path to .json netlist

    # 2. Place & Route
    r = await httpx.AsyncClient().post(f"{base}/tool/yosys/pnr_ice40", json={
        "netlist_json": netlist,
        "pcf_file": "/path/to/pins.pcf",
        "device": "hx8k",
        "package": "ct256",
        "freq_constraint_mhz": 50.0,
    })
    asc = r.json()["output_asc"]

    # 3. Pack bitstream
    r = await httpx.AsyncClient().post(f"{base}/tool/yosys/pack_ice40_bitstream", json={
        "asc_file": asc,
    })
    print("Bitstream:", r.json()["output_bin"])

asyncio.run(main())
```

---

## Development

```bash
# Install all dependencies
make install

# Build VSCode extension
make build

# Watch mode (extension)
make watch

# Lint TypeScript
make lint

# Clean build artifacts
make clean
```

### Running Tests

Each Python package uses `pytest`. From a package directory:

```bash
cd packages/llm-router
uv run pytest
```

---

## Environment Variables

### LLM Router (`packages/llm-router/.env`)

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for Claude backend |
| `OPENAI_API_KEY` | — | Required for OpenAI embeddings in datasheet-mcp |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.2` | Default Ollama model |

### Quartus MCP

| Variable | Default | Description |
|---|---|---|
| `QUARTUS_SH` | `quartus_sh` | Path to Quartus shell binary |

### Collab Server (`packages/collab-server/.env`)

| Variable | Default | Description |
|---|---|---|
| `COLLAB_JWT_SECRET` | random | JWT signing secret (set in production) |
| `COLLAB_API_KEY` | — | Bootstrap admin API key |
| `COLLAB_TEAM_ID` | `default` | Team namespace for global knowledge |
| `COLLAB_QDRANT_URL` | `http://localhost:6333` | Qdrant vector DB URL |
| `COLLAB_STORAGE_PATH` | `./collab_qdrant_storage` | Local Qdrant storage path |
| `COLLAB_PORT` | `8766` | Server port |
| `COLLAB_RUN_TTL_HOURS` | `24` | Agent run result retention time |

---

## License

MIT
