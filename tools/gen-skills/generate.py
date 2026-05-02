#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml>=6.0",
#   "jinja2>=3.1",
# ]
# ///
"""Generate host-specific adapter files from MCP `skill.yaml` manifests.

Reads:
    packages/mcp-servers/*/skill.yaml

Writes (committed to git — CI fails on diff):
    .claude/skills/<skill_id>/SKILL.md          # per-skill Claude Code skill
    .opencode/commands/<opencode_command>.md    # per-skill OpenCode command
    build/codex-config.toml                     # all MCPs in one snippet
    build/claude-mcp-config.json                # all MCPs in one snippet
    build/opencode-mcp-config.yaml              # all MCPs in one snippet

Invocation:
    uv run --script tools/gen-skills/generate.py
    uv run --script tools/gen-skills/generate.py --check    # dry-run / drift detection
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT  = Path(__file__).resolve().parents[2]
MCP_GLOB   = "packages/mcp-servers/*/skill.yaml"
TEMPLATES  = Path(__file__).parent / "templates"

OUT_CLAUDE_SKILLS_DIR   = REPO_ROOT / ".claude" / "skills"
OUT_OPENCODE_CMDS_DIR   = REPO_ROOT / ".opencode" / "commands"
OUT_BUILD_DIR           = REPO_ROOT / "build"
OUT_CODEX_TOML          = OUT_BUILD_DIR / "codex-config.toml"
OUT_CLAUDE_MCP_JSON     = OUT_BUILD_DIR / "claude-mcp-config.json"
OUT_OPENCODE_MCP_YAML   = OUT_BUILD_DIR / "opencode-mcp-config.yaml"


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Skill:
    id:                str
    tool:              str
    summary:           str
    claude_trigger:    str | None = None
    opencode_command:  str | None = None
    codex_alias:       str | None = None
    notes:             str | None = None


@dataclass
class MCP:
    mcp_name:   str
    binary:     str
    transport:  str
    env:        dict[str, list[str]] = field(default_factory=lambda: {"required": [], "optional": []})
    skills:     list[Skill]          = field(default_factory=list)

    @classmethod
    def from_yaml(cls, payload: dict[str, Any], source: Path) -> "MCP":
        try:
            return cls(
                mcp_name=payload["mcp_name"],
                binary=payload["binary"],
                transport=payload.get("transport", "stdio"),
                env=_normalize_env(payload.get("env")),
                skills=[_parse_skill(s) for s in payload.get("skills", [])],
            )
        except KeyError as exc:
            raise ValueError(f"{source}: missing required key {exc}") from None


def _normalize_env(env: Any) -> dict[str, list[str]]:
    env = env or {}
    return {
        "required": list(env.get("required") or []),
        "optional": list(env.get("optional") or []),
    }


def _parse_skill(raw: dict[str, Any]) -> Skill:
    return Skill(
        id=raw["id"],
        tool=raw["tool"],
        summary=raw["summary"],
        claude_trigger=raw.get("claude_trigger"),
        opencode_command=raw.get("opencode_command"),
        codex_alias=raw.get("codex_alias"),
        notes=raw.get("notes"),
    )


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_manifests(repo_root: Path = REPO_ROOT) -> list[MCP]:
    """Load every `packages/mcp-servers/*/skill.yaml` in the repo."""
    mcps: list[MCP] = []
    for path in sorted(repo_root.glob(MCP_GLOB)):
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        mcps.append(MCP.from_yaml(raw, path))
    return mcps


# ── Rendering ────────────────────────────────────────────────────────────────

def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    return env


def render_skill_files(mcps: list[MCP], *, env: Environment) -> dict[Path, str]:
    """One SKILL.md per skill (Claude Code) + one command.md per skill (OpenCode)."""
    out: dict[Path, str] = {}
    skill_tpl   = env.get_template("SKILL.md.j2")
    command_tpl = env.get_template("command.md.j2")
    for mcp in mcps:
        for skill in mcp.skills:
            out[OUT_CLAUDE_SKILLS_DIR / skill.id / "SKILL.md"] = skill_tpl.render(mcp=mcp, skill=skill)
            cmd_name = skill.opencode_command or skill.id
            out[OUT_OPENCODE_CMDS_DIR / f"{cmd_name}.md"]      = command_tpl.render(mcp=mcp, skill=skill)
    return out


def render_config_snippets(mcps: list[MCP], *, env: Environment) -> dict[Path, str]:
    """Whole-fleet config snippets — one file each for Claude Code, Codex, OpenCode."""
    out: dict[Path, str] = {}
    out[OUT_CODEX_TOML]        = env.get_template("codex.toml.j2").render(mcps=mcps)
    out[OUT_CLAUDE_MCP_JSON]   = _format_json(env.get_template("claude-mcp.json.j2").render(mcps=mcps))
    out[OUT_OPENCODE_MCP_YAML] = env.get_template("opencode-mcp.yaml.j2").render(mcps=mcps)
    return out


def _format_json(text: str) -> str:
    """Round-trip JSON through json.loads/dumps to normalise whitespace."""
    try:
        return json.dumps(json.loads(text), indent=2, sort_keys=False) + "\n"
    except json.JSONDecodeError:
        # Template produced something non-strict; return verbatim and let CI flag.
        return text


# ── File I/O ─────────────────────────────────────────────────────────────────

def write_files(files: dict[Path, str], *, check: bool) -> list[Path]:
    """Write files; when ``check=True``, compare without writing and return drifted paths."""
    drifted: list[Path] = []
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        if existing != content:
            drifted.append(path)
            if not check:
                path.write_text(content, encoding="utf-8")
    return drifted


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if generated files differ from on-disk content (drift check).",
    )
    args = parser.parse_args(argv)

    mcps = load_manifests()
    if not mcps:
        print("No packages/mcp-servers/*/skill.yaml found — nothing to generate.", file=sys.stderr)
        return 0

    print(f"Loaded {len(mcps)} MCP manifests ({sum(len(m.skills) for m in mcps)} skills).", file=sys.stderr)

    env   = _env()
    files = {**render_skill_files(mcps, env=env), **render_config_snippets(mcps, env=env)}

    drifted = write_files(files, check=args.check)
    if args.check and drifted:
        print("Drift detected in:", file=sys.stderr)
        for p in drifted:
            print(f"  {p.relative_to(REPO_ROOT)}", file=sys.stderr)
        print(
            "Run without --check to regenerate, then commit the result.",
            file=sys.stderr,
        )
        return 1

    if not args.check:
        action = "updated" if drifted else "already in sync"
        print(f"{len(files)} files {action} ({len(drifted)} changed).", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
