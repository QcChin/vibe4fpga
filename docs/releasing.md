# Releasing

All 9 Python packages (3 shared + 7 MCPs — `mcp-testkit` is dev-only and
not published) plus the Rust MCP ship from a single tag. The workflow
lives at [`.github/workflows/release.yml`](../.github/workflows/release.yml).

The Python `waveform-mcp` package was absorbed into the Rust `waveform-mcp-rs`
crate in v0.3.0 — don't add it back to the release matrix.

## One-time PyPI setup

PyPI trusted publishing requires each project to exist on PyPI and name
this repo + workflow as a trusted publisher. Do this once per package:

1. Sign up / log in at https://pypi.org
2. Create the project placeholder:
   * Go to **Your projects → Publishing → Add a pending publisher**
   * Fill in:
     - PyPI Project Name: the `name =` from that package's `pyproject.toml`
       (e.g. `vibe4fpga-llm-client`, `fpga-project-mcp`, etc.)
     - Owner: your GitHub org / user (e.g. `naspter`)
     - Repository name: `vibe4fpga`
     - Workflow name: `release.yml`
     - Environment name: `release` (must match the `environment: release`
       line in the publish job)
3. Repeat for the other 9 packages.

Package names to register (check each `pyproject.toml` for the exact string):

| Path | PyPI name |
| ---- | --------- |
| `packages/shared/llm-client` | `vibe4fpga-llm-client` |
| `packages/shared/platform` | `vibe4fpga-platform` |
| `packages/mcp-servers/fpga-project-mcp` | `fpga-project-mcp` |
| `packages/mcp-servers/eda-bridge-mcp` | `eda-bridge-mcp` |
| `packages/mcp-servers/instrument-mcp` | `instrument-mcp` |
| `packages/mcp-servers/datasheet-mcp` | `datasheet-mcp` |
| `packages/mcp-servers/quartus-mcp` | `quartus-mcp` |
| `packages/mcp-servers/yosys-mcp` | `yosys-mcp` |
| `packages/mcp-servers/verify-mcp` | `vibe4fpga-verify-mcp` |

## GitHub environment

Create a `release` environment on the repo (Settings → Environments →
New environment → `release`). You can:

* Require reviewers before the publish job runs (recommended for the
  first release while trusted publishing is being validated)
* Add secrets if you need anything beyond OIDC — the workflow as written
  needs **none**; `GITHUB_TOKEN` is auto-provided

## Cutting a release

```bash
# 1. Bump versions in all pyproject.toml files that changed since last tag.
#    Shared libs on their own cadence; MCPs stay in lockstep at v0.2.0.

# 2. Sanity check local state:
make gen-skills-check    # no drift
make test                # tier 1+2 green

# 3. Commit any last changes, push, and tag:
git tag -a v0.2.0 -m "Release 0.2.0"
git push origin develop
git push origin v0.2.0
```

The tag push triggers `.github/workflows/release.yml`. Jobs run in this
order:

```
preflight  ──┬─► python-build (10 packages, parallel)  ──► python-publish (OIDC)
             │
             └─► rust-build   (mac + win, parallel)     ──┐
                                                          │
                                                          ▼
                                                   github-release
```

A failure in any `python-build` matrix cell skips its corresponding
`python-publish` job but doesn't block the other nine. The Rust job is
independent. The GitHub release job runs only after both upstream legs
finish.

## Dry-run

Trigger manually with `workflow_dispatch` and set `dry_run=true`:

* Runs `preflight` + `python-build` + `rust-build`
* **Skips** `python-publish` and `github-release`

This is useful for smoke-testing wheel builds on a release candidate
branch without publishing.

## Package-name inconsistency note

The 8 pre-pivot MCPs publish under their bare name (`fpga-project-mcp`,
`eda-bridge-mcp`, ...); `verify-mcp` (added in the pivot) uses
`vibe4fpga-verify-mcp`. Renaming the 8 older ones to match would break
existing local `uv tool install` users; we're leaving them as-is. The
`configs/*` host snippets reference the *entry point* names (which are
all `<mcp>-mcp`), not the PyPI names, so users don't see the split.

## After publishing

* Verify installs from a clean machine:
  ```bash
  uv tool install fpga-project-mcp
  fpga-project-mcp --help
  ```
* Update `CHANGELOG.md` (if kept) with the new tag's delta
* Announce in whatever channel the team uses
