"""Exceptions raised by llm-client adapters and the registry."""

from __future__ import annotations


class AdapterError(Exception):
    """Base exception for all adapter-side failures."""


class AuthError(AdapterError):
    """Missing or invalid API key / credentials."""


class RateLimitError(AdapterError):
    """Upstream provider signalled rate-limit / quota exhaustion."""


class ConfigError(AdapterError):
    """Invalid configuration (unknown model key, malformed env var, etc.)."""


class MissingDependencyError(AdapterError):
    """The vendor SDK for the requested backend is not installed.

    Raised by the registry when e.g. the user selects ``gemini`` but
    ``google-generativeai`` is not installed. The ``extra`` attribute
    names the pip extra that would satisfy the dependency.
    """

    def __init__(self, backend: str, extra: str, original: ImportError) -> None:
        self.backend  = backend
        self.extra    = extra
        self.original = original
        super().__init__(
            f"Backend '{backend}' requires optional dependency. "
            f"Install with: pip install 'vibe4fpga-llm-client[{extra}]'  "
            f"(ImportError: {original})"
        )
