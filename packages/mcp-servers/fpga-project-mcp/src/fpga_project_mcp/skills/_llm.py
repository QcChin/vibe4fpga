"""Shared LLM-call helper for this MCP's skills.

Replaces the per-skill ``httpx.AsyncClient.post(f"{router_url}/chat", ...)``
pattern from the retired FastAPI llm-router era. Every skill in this package
goes through :func:`call_llm` so the switch to a different backend is a
one-line change in ``vibe4fpga_llm_client.adapter_from_env`` rather than
three skill-local edits.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vibe4fpga_llm_client import adapter_from_env


async def call_llm(
    messages:    list[dict],
    system:      str,
    model:       str = "claude",
    temperature: float = 0.3,
    max_tokens:  int   = 8192,
) -> str:
    """Non-streaming call that collects all chunks into a single string.

    Args:
        messages:    OpenAI-shaped ``[{"role": ..., "content": ...}]`` list.
        system:      system prompt (may be empty string; adapters treat "" as None).
        model:       model key understood by the llm-client registry
                     (e.g. ``"claude"``, ``"claude-opus"``, ``"gpt-4o"``).
        temperature: sampling temperature; skills default to 0.1 for deterministic
                     RTL and 0.3 for more creative reasoning.
        max_tokens:  upper bound passed through to the adapter.
    """
    adapter = adapter_from_env(model)
    chunks: list[str] = []
    async for chunk in adapter.complete(
        messages=messages,
        system=system or None,
        stream=False,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        chunks.append(chunk)
    return "".join(chunks)


def parse_json_response(text: str) -> Any:
    """Extract JSON from an LLM response, tolerating markdown code fences.

    Accepts both plain JSON and ````json ... ````` / ````` ... ````` fences.
    Raises :class:`json.JSONDecodeError` on genuinely malformed output so
    callers can surface a meaningful error to their tool consumer.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    return json.loads(cleaned.strip())
