"""Adapter factory — instantiate a :class:`BaseAdapter` from a model key + env.

The registry keeps vendor SDK imports *lazy* so that callers who only use
Claude don't pay the import cost of OpenAI / Gemini / etc., and so a missing
optional dependency only fails if the backend is actually requested.

Environment variables read:

* ``VIBE4FPGA_LLM``        — default backend key when ``model_key`` is None
* ``ANTHROPIC_API_KEY``    — Claude family, direct API (x-api-key)
* ``ANTHROPIC_AUTH_TOKEN`` — Claude family, Bearer-token proxy / OAuth
                             (alternative to ANTHROPIC_API_KEY; at least
                             one of the two must be set)
* ``ANTHROPIC_BASE_URL``   — Claude family, override endpoint (optional,
                             for custom proxies / gateways)
* ``OPENAI_API_KEY``       — OpenAI + DeepSeek (via openai_compat)
* ``OPENAI_BASE_URL``      — OpenAI, override endpoint (optional, for
                             third-party OpenAI-compatible aggregators)
* ``DEEPSEEK_API_KEY``     — explicit override for DeepSeek
* ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` — Gemini
* ``OLLAMA_BASE_URL``      — Ollama / RTLCoder HTTP endpoint (default ``http://localhost:11434``)
* ``OLLAMA_MODEL``         — model name for generic Ollama backend (default ``llama3.2``)

Precedence when both ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_AUTH_TOKEN`` are
set: ``ANTHROPIC_API_KEY`` wins (matches the Anthropic SDK's own priority).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .errors import AuthError, ConfigError, MissingDependencyError

if TYPE_CHECKING:
    from .base import BaseAdapter


# Canonical model-key → (backend, extra) registry.
# ``backend`` names the internal module; ``extra`` is the pip extra that
# provides the SDK (or None for stdlib-only backends).
_REGISTRY: dict[str, tuple[str, str | None]] = {
    # Claude
    "claude":        ("claude", "claude"),
    "claude-sonnet": ("claude", "claude"),
    "claude-opus":   ("claude", "claude"),
    "claude-haiku":  ("claude", "claude"),

    # OpenAI
    "openai":        ("openai", "openai"),
    "gpt-4o":        ("openai", "openai"),
    "gpt-4o-mini":   ("openai", "openai"),
    "o3":            ("openai", "openai"),
    "o3-mini":       ("openai", "openai"),

    # DeepSeek (OpenAI-compatible endpoint)
    "deepseek":      ("deepseek", "deepseek"),
    "deepseek-r1":   ("deepseek", "deepseek"),
    "deepseek-chat": ("deepseek", "deepseek"),

    # Gemini
    "gemini":             ("gemini", "gemini"),
    "gemini-flash":       ("gemini", "gemini"),
    "gemini-pro":         ("gemini", "gemini"),
    "gemini-1.5-flash":   ("gemini", "gemini"),
    "gemini-1.5-pro":     ("gemini", "gemini"),

    # Ollama — local, httpx only (no vendor SDK extra)
    "ollama":        ("ollama", None),

    # RTLCoder / CodeV family (Ollama-hosted, instruction-formatted)
    "rtlcoder":      ("rtlcoder", None),
    "rtlcoder-7b":   ("rtlcoder", None),
    "rtlcoder-13b":  ("rtlcoder", None),
    "rtlcoder-q4":   ("rtlcoder", None),
    "codev":         ("rtlcoder", None),
    "codev-v2":      ("rtlcoder", None),
}


def supported_models() -> list[str]:
    """Return the list of recognised ``model_key`` values."""
    return sorted(_REGISTRY.keys())


def adapter_from_env(model_key: str | None = None) -> "BaseAdapter":
    """Build an adapter from ``model_key`` + environment variables.

    Args:
        model_key: e.g. ``"claude"``, ``"gpt-4o"``, ``"deepseek-r1"``, ``"ollama"``.
            When None, reads ``VIBE4FPGA_LLM`` env var (fallback ``"claude"``).

    Raises:
        ConfigError: unknown ``model_key``.
        AuthError:   required API key env var is missing.
        MissingDependencyError: the vendor SDK extra is not installed.
    """
    key = (model_key or os.getenv("VIBE4FPGA_LLM") or "claude").strip().lower()
    if key not in _REGISTRY:
        raise ConfigError(
            f"Unknown model key '{key}'. Supported: {', '.join(supported_models())}"
        )

    backend, extra = _REGISTRY[key]

    # ── Claude ─────────────────────────────────────────────────────────────
    if backend == "claude":
        api_key    = os.getenv("ANTHROPIC_API_KEY",    "").strip() or None
        auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip() or None
        base_url   = os.getenv("ANTHROPIC_BASE_URL",   "").strip() or None
        if not api_key and not auth_token:
            raise AuthError(
                "Neither ANTHROPIC_API_KEY nor ANTHROPIC_AUTH_TOKEN is set. "
                "Set ANTHROPIC_API_KEY for direct api.anthropic.com access, "
                "or ANTHROPIC_AUTH_TOKEN for Bearer-token proxies / gateways "
                "(optionally combined with ANTHROPIC_BASE_URL)."
            )
        try:
            from .claude import ClaudeAdapter
        except ImportError as exc:
            raise MissingDependencyError("claude", extra or "claude", exc) from exc
        return ClaudeAdapter(
            api_key=api_key,
            auth_token=auth_token,
            base_url=base_url,
            model_key=key,
        )

    # ── OpenAI ─────────────────────────────────────────────────────────────
    if backend == "openai":
        api_key  = os.getenv("OPENAI_API_KEY",  "").strip()
        base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
        if not api_key:
            raise AuthError("OPENAI_API_KEY is not set")
        try:
            from .openai_compat import OpenAIAdapter
        except ImportError as exc:
            raise MissingDependencyError("openai", extra or "openai", exc) from exc
        return OpenAIAdapter(api_key=api_key, model_key=key, base_url=base_url)

    # ── DeepSeek (OpenAI-compatible endpoint, think-block stripping) ────────
    if backend == "deepseek":
        api_key = (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise AuthError("DEEPSEEK_API_KEY (or OPENAI_API_KEY fallback) is not set")
        try:
            from .deepseek import DeepSeekAdapter
        except ImportError as exc:
            raise MissingDependencyError("deepseek", extra or "deepseek", exc) from exc
        return DeepSeekAdapter(api_key=api_key, model_key=key)

    # ── Gemini ─────────────────────────────────────────────────────────────
    if backend == "gemini":
        api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
        if not api_key:
            raise AuthError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
        try:
            from .gemini import GeminiAdapter
        except ImportError as exc:
            raise MissingDependencyError("gemini", extra or "gemini", exc) from exc
        return GeminiAdapter(api_key=api_key, model_key=key)

    # ── Ollama (local HTTP, no API key) ─────────────────────────────────────
    if backend == "ollama":
        from .ollama import OllamaAdapter
        return OllamaAdapter(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        )

    # ── RTLCoder / CodeV (Ollama + instruction wrapper) ─────────────────────
    if backend == "rtlcoder":
        from .rtlcoder import RTLCoderAdapter
        return RTLCoderAdapter(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=key,
        )

    # Unreachable given the registry guard above, but defensive.
    raise ConfigError(f"No factory branch for backend '{backend}'")
