"""LLM backend adapters.

Each adapter will implement:

    async def complete(
        messages: list[dict],
        stream: bool = True,
    ) -> AsyncGenerator[str, None]: ...

Planned adapters (Phase 1):
  claude.py   — Anthropic API (primary: code generation, complex reasoning)
  ollama.py   — Local Ollama HTTP (offline / private projects)

Planned adapters (Phase 2+):
  openai.py   — OpenAI GPT-4o / o3
  gemini.py   — Google Gemini Flash (low latency)
  deepseek.py — DeepSeek API (cost-sensitive)

Phase 4 adapters:
  rtlcoder.py — RTLCoder / CodeV (Ollama-hosted fine-tuned HDL model)
"""
