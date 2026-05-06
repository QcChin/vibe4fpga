"""OpenAI-compatible adapter (openai>=1.20.0).

Supports models: gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-4, gpt-3.5-turbo.

Recommended routing:
  - Complex code / vision  → gpt-4o
  - Fast / cheap tasks     → gpt-4o-mini
  - Legacy compatibility   → gpt-3.5-turbo

Multimodal:
  Anthropic-style content lists (type="image", source.type="base64") are
  automatically converted to OpenAI vision format (type="image_url" with a
  data: URL).
"""

from __future__ import annotations

import base64
from typing import AsyncGenerator

from openai import AsyncOpenAI

from .base import BaseAdapter

MODEL_MAP: dict[str, str] = {
    "openai":       "gpt-4o",
    "gpt4o":        "gpt-4o",
    "gpt-4o":       "gpt-4o",
    "gpt4o-mini":   "gpt-4o-mini",
    "gpt-4o-mini":  "gpt-4o-mini",
    "gpt-4-turbo":  "gpt-4-turbo",
    "gpt-4":        "gpt-4",
    "gpt35":        "gpt-3.5-turbo",
}


def _convert_content(content: str | list) -> str | list:
    """Convert Anthropic-style multimodal content to OpenAI vision format.

    Anthropic image part schema::

        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "<base64-string>",
            },
        }

    OpenAI image_url part schema::

        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,<base64-string>"},
        }

    Text parts (type="text") are passed through unchanged.
    Plain string content is returned as-is.
    """
    if isinstance(content, str):
        return content

    converted: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            converted.append(part)
            continue

        part_type = part.get("type")

        if part_type == "text":
            converted.append({"type": "text", "text": part.get("text", "")})

        elif part_type == "image":
            source = part.get("source", {})
            src_type = source.get("type", "base64")

            if src_type == "base64":
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                data_url = f"data:{media_type};base64,{data}"
            elif src_type == "url":
                data_url = source.get("url", "")
            else:
                # Fallback: treat source data as raw base64
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                data_url = f"data:{media_type};base64,{data}"

            converted.append({"type": "image_url", "image_url": {"url": data_url}})

        else:
            # Unknown part type — pass through so the API can surface the error
            converted.append(part)

    return converted


def _prepare_messages(
    messages: list[dict],
    system: str | None,
) -> list[dict]:
    """Build the final OpenAI messages list.

    The optional system prompt is prepended as a system-role message.
    Anthropic-style multimodal content is converted in each message.
    """
    result: list[dict] = []

    if system:
        result.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = _convert_content(msg.get("content", ""))
        result.append({"role": role, "content": content})

    return result


class OpenAIAdapter(BaseAdapter):
    """Adapter for OpenAI GPT models via the openai>=1.20.0 AsyncOpenAI client."""

    def __init__(
        self,
        api_key: str,
        model_key: str = "openai",
        base_url: str | None = None,
    ) -> None:
        """Initialise the adapter.

        Args:
            api_key:   OpenAI API key.
            model_key: Logical model alias; resolved through MODEL_MAP.
                       Falls back to ``gpt-4o`` for unknown keys.
            base_url:  Optional endpoint override for third-party
                       OpenAI-compatible aggregators/proxies. Leave ``None``
                       to use the public OpenAI endpoint.
        """
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._model = MODEL_MAP.get(model_key, "gpt-4o")

    async def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks (streaming) or a single full response (non-streaming).

        Args:
            messages:    Conversation history; each dict has ``role`` and
                         ``content``.  Content may be a plain string or an
                         Anthropic-style multimodal list.
            system:      Optional system-prompt string.
            stream:      When ``True`` (default) text chunks are yielded as
                         they arrive from the API.
            temperature: Sampling temperature (0.0–2.0).
            max_tokens:  Maximum tokens in the completion.

        Yields:
            str: Successive text fragments (streaming) or the complete
                 response text (non-streaming).
        """
        openai_messages = _prepare_messages(messages, system)

        kwargs: dict = dict(
            model=self._model,
            messages=openai_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )

        if stream:
            response = await self._client.chat.completions.create(**kwargs)
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
        else:
            kwargs["stream"] = False
            response = await self._client.chat.completions.create(**kwargs)
            yield response.choices[0].message.content or ""
