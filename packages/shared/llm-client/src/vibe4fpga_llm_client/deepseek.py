"""DeepSeek adapter.

DeepSeek exposes an OpenAI-compatible REST API at https://api.deepseek.com,
so this adapter reuses the ``openai`` SDK (>=1.20.0) with a ``base_url``
override.

Supports models: deepseek-chat, deepseek-coder, deepseek-reasoner (R1).

MODEL_MAP:
  - "deepseek"         → deepseek-chat   (general assistant)
  - "deepseek-chat"    → deepseek-chat
  - "deepseek-coder"   → deepseek-coder  (code-optimised)
  - "deepseek-r1"      → deepseek-reasoner (chain-of-thought / R1)

DeepSeek R1 note:
  The ``deepseek-reasoner`` model emits its reasoning chain wrapped in
  ``<think>...</think>`` tags before the final answer.  These tags (and
  their content) are stripped from the yielded output so downstream
  consumers only receive the clean answer text.  A simple streaming
  state-machine is used so the tags are handled correctly even when they
  are split across multiple chunks.
"""

from __future__ import annotations

import re
from typing import AsyncGenerator

from openai import AsyncOpenAI

from .base import BaseAdapter
from .openai_compat import _convert_content, _prepare_messages  # re-use helpers

MODEL_MAP: dict[str, str] = {
    "deepseek":          "deepseek-chat",
    "deepseek-chat":     "deepseek-chat",
    "deepseek-coder":    "deepseek-coder",
    "deepseek-r1":       "deepseek-reasoner",
}

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Regex for stripping complete <think>...</think> blocks from a text buffer.
# re.DOTALL so '.' matches newlines inside the thinking block.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class _ThinkStripper:
    """Incremental state-machine that strips ``<think>...</think>`` tags.

    The R1 model streams tokens one-by-one, so the opening or closing tags
    may arrive split across multiple chunks.  This class buffers partial
    tag text and only yields "safe" content that is definitely outside a
    thinking block.

    States:
        outside  — normal output, yielded immediately.
        inside   — inside a <think> block, content is suppressed.
        partial  — buffering a potential tag boundary (could be ``<think>``
                   or ``</think>`` start).
    """

    _OPEN_TAG = "<think>"
    _CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self._state: str = "outside"  # "outside" | "inside" | "partial"
        self._buffer: str = ""

    def feed(self, chunk: str) -> str:
        """Process *chunk* and return the text that should be forwarded."""
        self._buffer += chunk
        output_parts: list[str] = []

        while self._buffer:
            if self._state == "outside":
                idx = self._buffer.find("<")
                if idx == -1:
                    # No '<' at all — safe to emit everything
                    output_parts.append(self._buffer)
                    self._buffer = ""
                elif idx > 0:
                    # Emit everything before the '<'
                    output_parts.append(self._buffer[:idx])
                    self._buffer = self._buffer[idx:]
                else:
                    # Buffer starts with '<' — check for known tags
                    if self._buffer.startswith(self._OPEN_TAG):
                        self._state = "inside"
                        self._buffer = self._buffer[len(self._OPEN_TAG):]
                    elif len(self._buffer) < len(self._OPEN_TAG) and self._OPEN_TAG.startswith(self._buffer):
                        # Could be the start of <think> — wait for more data
                        break
                    else:
                        # Not a recognised tag — emit the '<' and continue
                        output_parts.append("<")
                        self._buffer = self._buffer[1:]

            elif self._state == "inside":
                idx = self._buffer.find(self._CLOSE_TAG)
                if idx == -1:
                    # Check for a partial closing tag at the end of the buffer
                    partial_match_len = 0
                    for length in range(1, len(self._CLOSE_TAG)):
                        if self._buffer.endswith(self._CLOSE_TAG[:length]):
                            partial_match_len = length
                    # Discard everything except the potential partial close tag
                    self._buffer = self._buffer[len(self._buffer) - partial_match_len:]
                    break
                else:
                    # Found the closing tag — skip everything up to and including it
                    self._buffer = self._buffer[idx + len(self._CLOSE_TAG):]
                    self._state = "outside"

            else:
                break  # Should not happen; defensive exit

        return "".join(output_parts)

    def flush(self) -> str:
        """Return any remaining buffered content after the stream ends.

        If we ended mid-tag, the partial tag is emitted as-is (better than
        silently dropping it).
        """
        remaining = self._buffer
        self._buffer = ""
        self._state = "outside"
        return remaining if self._state != "inside" else ""


class DeepSeekAdapter(BaseAdapter):
    """Adapter for DeepSeek models via the OpenAI-compatible DeepSeek API."""

    def __init__(self, api_key: str, model_key: str = "deepseek") -> None:
        """Initialise the adapter.

        Args:
            api_key:   DeepSeek API key.
            model_key: Logical model alias; resolved through MODEL_MAP.
                       Falls back to ``deepseek-chat`` for unknown keys.
        """
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=_DEEPSEEK_BASE_URL,
        )
        self._model = MODEL_MAP.get(model_key, "deepseek-chat")
        self._is_reasoner = self._model == "deepseek-reasoner"

    async def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks (streaming) or a single full response (non-streaming).

        For the ``deepseek-reasoner`` (R1) model, ``<think>...</think>``
        reasoning tokens are stripped from the output so only the final
        answer is yielded.

        Args:
            messages:    Conversation history; each dict has ``role`` and
                         ``content``.  Anthropic-style multimodal content is
                         converted to OpenAI vision format automatically.
            system:      Optional system-prompt string.
            stream:      When ``True`` (default) text chunks are yielded as
                         they arrive from the API.
            temperature: Sampling temperature.
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
            stripper = _ThinkStripper() if self._is_reasoner else None
            response = await self._client.chat.completions.create(**kwargs)
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    text = delta.content
                    if stripper is not None:
                        text = stripper.feed(text)
                    if text:
                        yield text

            # Flush any partial tag left in the buffer
            if stripper is not None:
                remainder = stripper.flush()
                if remainder:
                    yield remainder

        else:
            kwargs["stream"] = False
            response = await self._client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content or ""
            if self._is_reasoner:
                # Strip all <think>...</think> blocks from the full response
                text = _THINK_BLOCK_RE.sub("", text).strip()
            yield text
