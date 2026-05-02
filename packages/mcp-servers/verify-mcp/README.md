# verify-mcp

MCP server for FPGA verification. Absorbs the `testbench_gen` and `verification`
skills from the retired monolithic `packages/skills/` package.

## Tools

| Tool                   | Purpose                                                      |
| ---------------------- | ------------------------------------------------------------ |
| `generate_testbench`   | LLM-driven SystemVerilog testbench synthesis                 |
| `score_verification`   | Multi-stage (lint / sim / formal / synth / spec) scoring     |

## Status

**Phase A — skeleton only.** Tool signatures are final; bodies raise
`NotImplementedError`. Phase B rewires the copied skill modules in
`src/verify_mcp/skills/` to call `vibe4fpga_llm_client.adapter_from_env()`.

## Install (dev)

```bash
cd packages/mcp-servers/verify-mcp
uv sync --all-extras
```

## Environment

| Var                | Required for           | Default |
| ------------------ | ---------------------- | ------- |
| `VIBE4FPGA_LLM`    | default LLM backend    | `claude` |
| `ANTHROPIC_API_KEY`| Claude backend         | —       |
| `OPENAI_API_KEY`   | OpenAI / DeepSeek backend | —   |
