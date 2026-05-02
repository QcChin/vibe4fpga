"""Google Gemini adapter (google-generativeai library).

Supports models: gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash.

Recommended routing:
  - Fast / low-latency tasks   → gemini-2.0-flash  (default)
  - Long-context / complex     → gemini-1.5-pro
  - Balanced cost + speed      → gemini-1.5-flash

Message format:
  The Gemini SDK uses "user" / "model" roles (not "assistant").  System prompts
  are passed via ``system_instruction`` to ``GenerativeModel``.

Multimodal:
  Anthropic-style base64 image parts are converted to Gemini ``inline_data``
  blobs.  URL-based images fall back to passing the raw URL string as text (the
  Files API is out of scope here).
"""

from __future__ import annotations

import base64
from typing import AsyncGenerator

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from .base import BaseAdapter

MODEL_MAP: dict[str, str] = {
    "gemini":          "gemini-2.0-flash",
    "gemini-flash":    "gemini-2.0-flash",
    "gemini-pro":      "gemini-1.5-pro",
    "gemini-1.5-pro":  "gemini-1.5-pro",
    "gemini-1.5-flash": "gemini-1.5-flash",
}

# Gemini role names differ from the OpenAI / Anthropic convention
_ROLE_MAP: dict[str, str] = {
    "user":      "user",
    "assistant": "model",
    "model":     "model",
    # system messages are handled separately via system_instruction
}


def _convert_part(part: dict) -> dict | str:
    """Convert a single Anthropic-style content part to a Gemini content part.

    Anthropic image part::

        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "<base64-string>",
            },
        }

    Gemini inline_data part::

        {
            "inline_data": {
                "mime_type": "image/png",
                "data": "<base64-string>",
            }
        }

    Text parts are returned as plain strings (Gemini accepts bare strings in
    the parts list).
    """
    part_type = part.get("type")

    if part_type == "text":
        return part.get("text", "")

    if part_type == "image":
        source = part.get("source", {})
        src_type = source.get("type", "base64")

        if src_type == "base64":
            return {
                "inline_data": {
                    "mime_type": source.get("media_type", "image/png"),
                    "data": source.get("data", ""),
                }
            }
        elif src_type == "url":
            # Gemini does not natively accept arbitrary URLs in the SDK's
            # inline_data path; surface the URL as descriptive text so the
            # model at least sees the reference.
            return f"[image url: {source.get('url', '')}]"

    # Unknown part — serialise to string as a safe fallback
    return str(part)


def _build_gemini_contents(messages: list[dict]) -> list[dict]:
    """Convert the canonical messages list to Gemini ``contents`` format.

    Each entry becomes::

        {"role": "user" | "model", "parts": [...]}

    System-role messages (if any slip through) are merged into the first user
    turn as a preamble rather than dropped silently.
    """
    contents: list[dict] = []
    system_preamble: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            # Collect any inline system messages to prepend to the first user turn
            text = content if isinstance(content, str) else " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
            system_preamble.append(text)
            continue

        gemini_role = _ROLE_MAP.get(role, "user")

        # Build parts list
        if isinstance(content, str):
            parts: list = [content]
        else:
            parts = [_convert_part(p) if isinstance(p, dict) else str(p) for p in content]

        # Prepend accumulated system text to the first user message
        if system_preamble and gemini_role == "user":
            preamble_text = "\n\n".join(system_preamble)
            parts = [preamble_text] + parts
            system_preamble.clear()

        contents.append({"role": gemini_role, "parts": parts})

    return contents


class GeminiAdapter(BaseAdapter):
    """Adapter for Google Gemini models via the google-generativeai SDK."""

    def __init__(self, api_key: str, model_key: str = "gemini") -> None:
        """Initialise the adapter.

        Args:
            api_key:   Google AI Studio API key (or Vertex AI key when using
                       the same SDK in Vertex mode).
            model_key: Logical model alias; resolved through MODEL_MAP.
                       Falls back to ``gemini-2.0-flash`` for unknown keys.
        """
        genai.configure(api_key=api_key)
        self._model_name = MODEL_MAP.get(model_key, "gemini-2.0-flash")

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
            system:      Optional system-prompt string passed as
                         ``system_instruction`` to the model constructor.
            stream:      When ``True`` (default) text chunks are yielded as
                         they arrive from the API.
            temperature: Sampling temperature (0.0–1.0 for Gemini).
            max_tokens:  Maximum output tokens (``max_output_tokens`` in
                         Gemini's ``GenerationConfig``).

        Yields:
            str: Successive text fragments (streaming) or the complete
                 response text (non-streaming).
        """
        generation_config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        model_kwargs: dict = dict(
            model_name=self._model_name,
            generation_config=generation_config,
        )
        if system:
            model_kwargs["system_instruction"] = system

        model = genai.GenerativeModel(**model_kwargs)
        contents = _build_gemini_contents(messages)

        if stream:
            response = await model.generate_content_async(
                contents,
                stream=True,
            )
            async for chunk in response:
                text = chunk.text
                if text:
                    yield text
        else:
            response = await model.generate_content_async(
                contents,
                stream=False,
            )
            yield response.text or ""
