"""Claude (Anthropic) adapter.

Recommended routing:
  - Code generation → claude-sonnet-4-6 (strong reasoning, long context)
  - Complex analysis → claude-opus-4-6
  - Fast completion  → claude-haiku-4-5-20251001
"""

from __future__ import annotations

from typing import AsyncGenerator

from anthropic import AsyncAnthropic

from .base import BaseAdapter

MODEL_MAP = {
    "claude":        "claude-sonnet-4-6",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-opus":   "claude-opus-4-6",
    "claude-haiku":  "claude-haiku-4-5-20251001",
}


class ClaudeAdapter(BaseAdapter):
    def __init__(self, api_key: str, model_key: str = "claude") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = MODEL_MAP.get(model_key, "claude-sonnet-4-6")

    async def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        kwargs: dict = dict(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if system:
            kwargs["system"] = system

        if stream:
            async with self._client.messages.stream(**kwargs) as s:
                async for text in s.text_stream:
                    yield text
        else:
            response = await self._client.messages.create(**kwargs)
            yield response.content[0].text
