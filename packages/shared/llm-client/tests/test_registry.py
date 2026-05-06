"""Registry auth-wiring tests — all pure-logic, no network calls.

Covers the three Claude auth paths (api_key / auth_token / both) plus the
OpenAI base_url passthrough. Each test clears and re-seeds the relevant env
vars via ``monkeypatch`` and patches out ``AsyncAnthropic`` / ``AsyncOpenAI``
so no SDK construction hits the real network.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from vibe4fpga_llm_client.errors import AuthError
from vibe4fpga_llm_client.registry import adapter_from_env


_CLAUDE_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
_OPENAI_ENVS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")


@pytest.fixture(autouse=True)
def _clear_llm_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with a clean env — avoids flakes from user shell state."""
    for name in _CLAUDE_ENVS + _OPENAI_ENVS + ("VIBE4FPGA_LLM",):
        monkeypatch.delenv(name, raising=False)


# ─────────────────────────────────────────────────────────────── Claude paths

def test_claude_uses_api_key_when_only_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-api")
    with patch("vibe4fpga_llm_client.claude.AsyncAnthropic") as MockClient:
        adapter_from_env("claude")
        kwargs = MockClient.call_args.kwargs
        assert kwargs == {"api_key": "sk-api"}


def test_claude_uses_auth_token_when_only_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer-xyz")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example/anthropic")
    with patch("vibe4fpga_llm_client.claude.AsyncAnthropic") as MockClient:
        adapter_from_env("claude")
        kwargs = MockClient.call_args.kwargs
        assert kwargs == {
            "auth_token": "bearer-xyz",
            "base_url":   "https://proxy.example/anthropic",
        }


def test_claude_api_key_wins_when_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY",    "sk-api")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer-xyz")
    with patch("vibe4fpga_llm_client.claude.AsyncAnthropic") as MockClient:
        adapter_from_env("claude")
        kwargs = MockClient.call_args.kwargs
        # Both are forwarded — SDK itself prefers api_key. We assert both
        # reach the SDK so the user gets whatever the SDK's policy is.
        assert kwargs["api_key"]    == "sk-api"
        assert kwargs["auth_token"] == "bearer-xyz"


def test_claude_raises_when_neither_auth_set() -> None:
    with pytest.raises(AuthError, match="Neither ANTHROPIC_API_KEY nor ANTHROPIC_AUTH_TOKEN"):
        adapter_from_env("claude")


# ─────────────────────────────────────────────────────────────── OpenAI paths

def test_openai_base_url_passthrough_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY",  "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://aggregator.example/v1")
    with patch("vibe4fpga_llm_client.openai_compat.AsyncOpenAI") as MockClient:
        adapter_from_env("gpt-4o")
        kwargs = MockClient.call_args.kwargs
        assert kwargs == {
            "api_key":  "sk-openai",
            "base_url": "https://aggregator.example/v1",
        }
