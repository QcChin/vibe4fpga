# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository shape

MCP-first monorepo: 8 MCP servers (7 Python + 1 Rust) that give agent hosts (Claude Code / Codex CLI / OpenCode) tools for an FPGA workflow — project scanning, spec→RTL, code review, timing fixes, testbench gen, waveform/scope analysis, datasheet RAG, and EDA bridging (Vivado / Quartus / Yosys / Verilator / Icarus). The Rust MCP `waveform-mcp-rs` absorbed the former Python `waveform-mcp` package in v0.3.0; it now covers VCD/FST parsing, AXI4 decoding, RTL source mapping, and the LLM-backed `debug_waveform` skill (via a built-in Anthropic HTTP client). Plus 3 shared Python libs:
- `packages/shared/llm-client/` — streaming adapters (`claude` / `gpt-4o` / `deepseek` / `gemini` / `ollama` / `rtlcoder`), selected via `VIBE4FPGA_LLM` + matching API key env var.
- `packages/shared/platform/` — cross-platform scratch paths, tool discovery, `async run()` that forces UTF-8 in child env and applies `\\?\` long-path prefix on Windows.
- `packages/shared/mcp-testkit/` — `stdio_server_spawn("<mcp-name>")` fixture for Tier-1 smoke tests (dev-only, not published).

See `docs/architecture.md` for the full picture.

## Common commands

Always run from the repo root unless noted.

| Command | What it does |
| ------- | ------------ |
| `make install` | Install `uv` (if missing) and `uv sync --all-extras` every Python package. First-time bootstrap. |
| `make sync` | `uv sync` all Python packages (after dep changes). |
| `make test` | Run `pytest -q` in every Python package, then `cargo test --locked` for `waveform-mcp-rs`. Stops on first failure. |
| `make gen-skills` | Regenerate `.claude/skills/*/SKILL.md`, `.opencode/commands/*.md`, and `configs/*` from each MCP's `skill.yaml`. |
| `make gen-skills-check` | CI drift check — fails if any generated file is out of date. |
| `make dev-<mcp-name>` | Spawn a single MCP over stdio for ad-hoc debugging (e.g. `make dev-fpga-project-mcp`). |

Per-package operations (inside any `packages/.../<pkg>` directory):
- `uv sync --all-extras` — sync dependencies for that package.
- `uv run pytest --tb=short -q` — run that package's tests only.
- `uv run pytest tests/test_scanner.py::test_foo` — single test.
- `uv tool install .` — expose the console-script (e.g. `fpga-project-mcp`) on PATH so host configs can launch it.

Rust: `cd packages/mcp-servers/waveform-mcp-rs && cargo test --locked`.

Ruff is configured (`select = ["E", "F", "I"]`) in each `pyproject.toml` — run `uv run ruff check .` inside a package if you want the linter.

## Python version

All Python packages target **3.11–3.12** (`requires-python = ">=3.11,<3.13"`). 3.13 is not yet supported by the `mcp` SDK. CI uses 3.12 on `macos-latest` and `windows-latest`.

## Per-MCP structure (follow this shape for new MCPs)

```
packages/mcp-servers/<name>/
  pyproject.toml          path-deps on the 3 shared libs; vendor SDKs behind [extras]
  skill.yaml              single source of truth for gen-skills
  src/<pkg>/
    server.py             FastMCP entrypoint + @mcp.tool() registrations
    skills/<skill>/       logic + prompts for each LLM-backed tool
    skills/_llm.py        tiny wrapper around llm-client + JSON parsing
  tests/
    test_smoke.py                Tier 1 — handshake + list_tools
    test_skill_yaml_parity.py    asserts declared skills exist as real tools
    test_*.py                    Tier 2 — pure-logic unit tests
```

Inter-package deps are path-editable (`[tool.uv.sources]` → `{ path = "../../shared/...", editable = true }`). Adding a new MCP means repeating those 3 entries.

## skill.yaml is the source of truth

`tools/gen-skills/generate.py` reads every `packages/mcp-servers/*/skill.yaml` and writes:
- `.claude/skills/<id>/SKILL.md` (Claude Code format)
- `.opencode/commands/<name>.md` (OpenCode format)
- `configs/claude-mcp-config.json`, `configs/codex-config.toml`, `configs/opencode-mcp-config.yaml` (whole-fleet host snippets)

**Never hand-edit the generated files** — edit `skill.yaml` then run `make gen-skills`. The generator is YAML-only (no MCP imports) so it works on a fresh clone before anything is installed; per-MCP `test_skill_yaml_parity.py` is what asserts the declaration matches the real `list_tools()` output. CI runs `--check` and fails on drift.

## Cross-platform rules (enforced by CI)

1. **No `/tmp` hardcodes.** `grep -RIn '"/tmp' packages/mcp-servers packages/shared` must return zero hits — use `vibe4fpga_platform.scratch_file(suffix)` / `scratch_dir(prefix)` instead. The `no-tmp-hardcode` CI job fails the build otherwise.
2. **All subprocess calls go through `vibe4fpga_platform.run()`.** It forces `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` in the child env (prevents GBK corruption on CN Windows), adds `\\?\` long-path prefix when Windows paths exceed 240 chars, and surfaces timeouts as `ProcessTimeoutError`.
3. **Tool discovery via `find_tool` / `require_tool`.** Respects `PATHEXT` on Windows and vendor install-hint env vars (`VIVADO_ROOT`, `QUARTUS_SH`, etc.). Don't hardcode executable names.
4. **Windows is a Tier-1 target.** Dev often happens on macOS but CI runs the full matrix on `macos-latest` × `windows-latest` (minus `quartus-mcp × macOS`).

## Testing tiers

| Tier | Scope |
| ---- | ----- |
| 1. Smoke | Every MCP: stdio handshake + `list_tools` returns the expected set. |
| 2. Pure-logic unit | MCPs with in-process logic (scanner, detector, scorer). Deterministic, no LLM, no network, no external tools. |
| 3. Tool-conditional | `eda-bridge`, `quartus`, `yosys`, `instrument` — skipped unless the external tool is on the runner. |
| 4. LLM smoke | Env-gated on `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`; asserts response shape only. |

`pytest` exit code 5 (no tests collected) is accepted by CI during bring-up — don't let that mask missing test files you expected to add.

## Release

Single-tag release for all 9 published packages (`mcp-testkit` is dev-only) + the Rust crate. Bump versions, `make gen-skills-check && make test`, then `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`. Workflow is `.github/workflows/release.yml` — uses PyPI trusted publishing via the `release` GitHub environment; no API tokens to manage. See `docs/releasing.md` for the PyPI pending-publisher setup that has to happen once per package.

Package-name quirk: 7 pre-pivot Python MCPs publish under their bare name (`fpga-project-mcp`, ...); `verify-mcp` publishes as `vibe4fpga-verify-mcp`. Don't try to "fix" this — it would break existing `uv tool install` users.
