# vibe4fpga-llm-client

Shared LLM adapter library for the vibe4fpga MCP servers. Provides a single
`BaseAdapter.complete()` streaming contract over:

| Backend      | `model_key`                              | Extra       | SDK                        |
| ------------ | ---------------------------------------- | ----------- | -------------------------- |
| Anthropic    | `claude`, `claude-{sonnet,opus,haiku}`   | `[claude]`  | `anthropic`                |
| OpenAI       | `openai`, `gpt-4o{,-mini}`, `o3{,-mini}` | `[openai]`  | `openai`                   |
| DeepSeek     | `deepseek`, `deepseek-r1`, `-chat`       | `[deepseek]`| `openai` (DeepSeek API)    |
| Gemini       | `gemini`, `gemini-1.5-{flash,pro}`       | `[gemini]`  | `google-generativeai`      |
| Ollama       | `ollama`                                 | —           | `httpx` (local HTTP)       |
| RTLCoder/CodeV | `rtlcoder{,-7b,-13b,-q4}`, `codev{,-v2}` | —         | `httpx` + instruction wrap |

## Install

```bash
# Minimum (Ollama / RTLCoder only)
uv pip install vibe4fpga-llm-client

# Add vendor SDKs you need
uv pip install 'vibe4fpga-llm-client[claude,openai]'

# Everything
uv pip install 'vibe4fpga-llm-client[all]'
```

## Use

```python
from vibe4fpga_llm_client import adapter_from_env

adapter = adapter_from_env("claude")          # reads ANTHROPIC_API_KEY
async for chunk in adapter.complete(
    messages=[{"role": "user", "content": "Write a 4-bit counter in Verilog"}],
    system="You are an expert FPGA designer.",
    stream=True,
    temperature=0.3,
):
    print(chunk, end="")
```

## Environment variables

| Var                            | Consumed by              | Default                    |
| ------------------------------ | ------------------------ | -------------------------- |
| `VIBE4FPGA_LLM`                | `adapter_from_env(None)` | `claude`                   |
| `ANTHROPIC_API_KEY`            | Claude                   | (required)                 |
| `OPENAI_API_KEY`               | OpenAI, DeepSeek fallback| (required)                 |
| `DEEPSEEK_API_KEY`             | DeepSeek (preferred)     | falls back to `OPENAI_API_KEY` |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini              | (required)                 |
| `OLLAMA_BASE_URL`              | Ollama, RTLCoder         | `http://localhost:11434`   |
| `OLLAMA_MODEL`                 | Ollama (generic)         | `llama3.2`                 |

## Development

```bash
cd packages/shared/llm-client
uv sync --all-extras
uv run pytest
```
