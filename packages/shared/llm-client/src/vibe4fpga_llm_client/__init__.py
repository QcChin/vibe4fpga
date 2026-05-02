"""vibe4fpga-llm-client — shared LLM adapter library for MCP servers.

Public API:

    from vibe4fpga_llm_client import adapter_from_env, BaseAdapter

    adapter = adapter_from_env("claude")
    async for chunk in adapter.complete(messages=[...], system="..."):
        print(chunk, end="")

Individual adapters (for advanced use):

    from vibe4fpga_llm_client.claude import ClaudeAdapter
    from vibe4fpga_llm_client.ollama import OllamaAdapter
    ...
"""

from __future__ import annotations

from .base import BaseAdapter
from .errors import (
    AdapterError,
    AuthError,
    ConfigError,
    MissingDependencyError,
    RateLimitError,
)
from .registry import adapter_from_env, supported_models

__all__ = [
    "BaseAdapter",
    "AdapterError",
    "AuthError",
    "ConfigError",
    "MissingDependencyError",
    "RateLimitError",
    "adapter_from_env",
    "supported_models",
]

__version__ = "0.1.0"
