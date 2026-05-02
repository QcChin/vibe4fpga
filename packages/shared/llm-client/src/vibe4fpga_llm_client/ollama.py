"""Ollama local model adapter.

Recommended for offline / private projects.
Compatible with Llama, Qwen, Mistral, etc.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

from .base import BaseAdapter


class OllamaAdapter(BaseAdapter):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        all_messages: list[dict] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        payload = {
            "model": self._model,
            "messages": all_messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            data = json.loads(line)
                            if content := data.get("message", {}).get("content"):
                                yield content
            else:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                yield resp.json()["message"]["content"]
