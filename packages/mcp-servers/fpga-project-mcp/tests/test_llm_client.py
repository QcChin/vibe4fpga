"""Tier 2 — prove the shared LLM adapter layer actually loads.

The smoke tests in ``test_smoke.py`` only exercise the MCP handshake and
tool-list surface; they never reach any code path that imports
:mod:`vibe4fpga_llm_client.adapter_from_env`. That leaves a blind spot
where the registry, a vendor-SDK optional extra, or :class:`BaseAdapter`
could be broken in a way the smoke tests cannot catch.

These tests plug that hole with zero network traffic:

* They instantiate the **ollama** adapter — the only backend whose base
  install (httpx, stdlib) is guaranteed to be available regardless of
  which ``[claude]`` / ``[openai]`` / ``[gemini]`` extras the user picked.
* They exercise the registry's surface (``supported_models``) to catch
  any accidental registry-key typo across the six backends.

If the shared ``vibe4fpga-llm-client`` dependency ever grows an import
error (bad conditional import, missing abc method, etc.), this file will
fail before any end-user sees a broken tool call.
"""

from __future__ import annotations

import os

import pytest

from vibe4fpga_llm_client import (
    BaseAdapter,
    adapter_from_env,
    supported_models,
)
from vibe4fpga_llm_client.errors import MissingDependencyError


def test_supported_models_covers_every_backend() -> None:
    """Every documented backend key must be in the registry."""
    models = set(supported_models())
    expected_keys = {
        "claude", "claude-sonnet", "claude-opus", "claude-haiku",
        "openai", "gpt-4o", "gpt-4o-mini",
        "deepseek", "deepseek-r1",
        "gemini", "gemini-1.5-flash", "gemini-1.5-pro",
        "ollama",
        "rtlcoder", "codev",
    }
    missing = expected_keys - models
    assert not missing, f"Registry is missing expected model keys: {missing}"


def test_ollama_adapter_loads_without_network() -> None:
    """Ollama has no API key + no vendor SDK requirement — this works on a
    fresh install with zero extras, so it's the correct signal for
    "registry + BaseAdapter contract are intact"."""
    adapter = adapter_from_env("ollama")
    assert isinstance(adapter, BaseAdapter)
    # Did not touch the network — the constructor only stores config.


def test_claude_adapter_loads_when_extra_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the ``[claude]`` extra IS installed (CI + dev workflow baseline),
    the Claude adapter instantiates without reaching the network.

    When the extra is NOT installed, a clean :class:`MissingDependencyError`
    with an actionable ``pip install`` hint is raised — also acceptable.
    Anything else (raw ``ImportError``, opaque attribute error) fails the
    test loudly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    try:
        adapter = adapter_from_env("claude")
    except MissingDependencyError as exc:
        assert exc.extra == "claude"
        assert "pip install" in str(exc).lower()
        return
    assert isinstance(adapter, BaseAdapter)


def test_missing_api_key_surfaces_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """adapter_from_env('claude') with no ANTHROPIC_API_KEY → AuthError.

    Catches a regression where the registry silently swallows the missing
    key and returns a half-built adapter that explodes on first use.
    """
    from vibe4fpga_llm_client.errors import AuthError

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError, match="ANTHROPIC_API_KEY"):
        adapter_from_env("claude")


def test_unknown_model_key_surfaces_config_error() -> None:
    from vibe4fpga_llm_client.errors import ConfigError

    with pytest.raises(ConfigError, match="Unknown model key"):
        adapter_from_env("not-a-real-backend")


def test_env_var_override_picks_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """VIBE4FPGA_LLM env var wins when model_key is None."""
    monkeypatch.setenv("VIBE4FPGA_LLM", "ollama")
    adapter = adapter_from_env(None)
    # Ollama lives in httpx-only base deps; no SDK surprise.
    assert isinstance(adapter, BaseAdapter)
