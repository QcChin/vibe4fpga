"""RTLCoder adapter — Ollama-hosted RTLCoder / CodeV fine-tuned model.

RTLCoder uses a special instruction format:
  ### Instruction:\n{prompt}\n\n### Response:\n

Reference:
  - RTLCoder: https://github.com/hkust-zhiyao/RTLCoder
  - CodeV:    Verilog generation fine-tuned models

Design doc reference: Phase 4 — FPGA专用微调模型
"""

from __future__ import annotations

from typing import AsyncGenerator

from .ollama import OllamaAdapter


# RTLCoder prompt wrapper — wraps plain prompts in the expected format
_RTLCODER_TEMPLATE = "### Instruction:\n{prompt}\n\n### Response:\n"

# Model aliases recognized by this adapter
RTLCODER_MODELS = {
    "rtlcoder":      "rtlcoder",        # generic alias → configured model
    "rtlcoder-7b":   "rtlcoder:7b",
    "rtlcoder-13b":  "rtlcoder:13b",
    "codev":         "codev:latest",
    "codev-v2":      "codev:v2",
    "rtlcoder-q4":   "rtlcoder:q4_0",  # quantized variant for low-VRAM
}


class RTLCoderAdapter(OllamaAdapter):
    """Extends OllamaAdapter with RTLCoder instruction-format wrapping."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "rtlcoder",
        use_instruction_format: bool = True,
    ):
        # Resolve model alias
        ollama_model = RTLCODER_MODELS.get(model, model)
        super().__init__(base_url=base_url, model=ollama_model)
        self._use_instruction_format = use_instruction_format

    def _wrap_messages(self, messages: list[dict]) -> list[dict]:
        """Wrap the last user message in RTLCoder instruction format."""
        if not self._use_instruction_format:
            return messages

        wrapped = []
        for i, msg in enumerate(messages):
            if msg["role"] == "user" and i == len(messages) - 1:
                # Last user message → apply instruction format
                wrapped.append({
                    "role": "user",
                    "content": _RTLCODER_TEMPLATE.format(prompt=msg["content"]),
                })
            else:
                wrapped.append(msg)
        return wrapped

    async def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.1,   # RTLCoder works best at low temperature
        max_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        wrapped = self._wrap_messages(messages)
        async for chunk in super().complete(
            messages=wrapped,
            system=system,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk
