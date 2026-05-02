"""Abstract base class for LLM backend adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator


class BaseAdapter(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks (streaming) or a single full response (non-streaming)."""
        ...
