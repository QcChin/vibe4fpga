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
    max_tokens:  int   = 16384,
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
    """Extract JSON from an LLM response, tolerating markdown code fences,
    surrounding prose, and partial output.

    Looks first for a ```json ... ``` (or plain ``` ... ```) block anywhere in
    the response, then falls back to scanning for a balanced top-level array
    or object. Raises :class:`json.JSONDecodeError` only when neither
    approach yields valid JSON.
    """
    fenced = re.search(r"```\w*\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    cleaned = text.strip()
    cleaned = re.sub(r"^```\w*\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    return json.loads(cleaned.strip())
