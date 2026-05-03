# gen-skills

Generates host-specific adapter files from `packages/mcp-servers/*/skill.yaml`:

* `.claude/skills/<id>/SKILL.md` — one per skill, Claude Code format
* `.opencode/commands/<name>.md` — one per skill, OpenCode format
* `configs/claude-mcp-config.json` — all MCPs in one snippet for `~/.claude.json`
* `configs/codex-config.toml` — ditto for `~/.codex/config.toml`
* `configs/opencode-mcp-config.yaml` — ditto for OpenCode config

All outputs are committed. CI runs `--check` and fails on drift so
`skill.yaml` stays the single source of truth.

## Run

```bash
uv run --script tools/gen-skills/generate.py            # regenerate
uv run --script tools/gen-skills/generate.py --check    # drift check (CI)
```

Dependencies are declared inline (PEP 723) — no separate venv needed.

## Why YAML-only (no MCP imports)?

The generator runs before any MCP is installed (e.g. on a fresh clone's CI),
so it cannot introspect live tool schemas. Each MCP's per-package test
asserts that its `skill.yaml` matches its real `list_tools()` output, keeping
the declaration-vs-reality gap honest without coupling generator runtime to
the whole Python dependency tree.

## skill.yaml schema (excerpt)

```yaml
mcp_name: fpga-project-mcp
binary:   fpga-project-mcp          # console-script name on PATH
transport: stdio                    # stdio | http (stdio for MVP)
env:
  required: []                      # env vars the host MUST set
  optional: [VIBE4FPGA_LLM, OPENAI_API_KEY]
skills:
  - id: spec2rtl                    # Claude Code skill id (directory name)
    tool: spec_to_rtl               # MCP tool name (matches server.py)
    summary: "Convert natural-language FPGA spec into synthesizable Verilog."
    claude_trigger: "spec|rtl|verilog from english"   # optional
    opencode_command: spec2rtl      # optional; defaults to `id`
    codex_alias: s2r                # optional
    notes: |                        # optional free-form markdown
      Multi-stage pipeline: parse → ambiguity detect → RAG inject → gen → self-check.
```
