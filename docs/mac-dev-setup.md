# Mac dev setup (for a Windows target)

The MCPs run in production on Windows, but day-to-day development happens
on macOS. This guide describes how to write and test code on Mac with
realistic coverage of Windows-specific behaviour.

## Baseline install

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.12
brew install python@3.12

# Shared libs + all 9 MCPs in editable mode
make install

# Verify
make test
```

Apple Silicon and Intel are both fine; CI runs `macos-latest` which is
Apple Silicon.

## Tools you can install natively on Mac

| Tool | Install command | Notes |
| --- | --- | --- |
| Yosys + nextpnr + icepack | `brew install yosys nextpnr icestorm` | fully supported |
| Icarus Verilog | `brew install icarus-verilog` | fully supported |
| Verilator | `brew install verilator` | fully supported |
| pyvisa (py backend only) | `uv pip install pyvisa pyvisa-py` | limited SCPI protocols |
| Rust stable | `brew install rustup-init && rustup-init` | needed for `waveform-mcp-rs` |

## Tools you CANNOT run natively on Mac

* **Vivado** — no macOS build. Use a VM (UTM + Windows 11 on Apple Silicon
  works), a Windows dev box, or stub the tool calls out for the affected
  tests.
* **Quartus Prime** — same (Linux / Windows only). CI already excludes
  `quartus-mcp × macos-latest`; your local `make test` skips the
  Quartus-gated test by design.
* **NI-VISA / Keysight VISA** — instrument backends are Windows /
  Linux only. pyvisa-py fills in partially (Python-native serial /
  socket), but OEM scope control usually needs the vendor driver.

## Gating tests on tool availability

All MCPs use the same `@pytest.mark.skipif(not find_tool("..."), reason=...)`
pattern for Tier 3 tests that actually invoke an external tool. On a clean
Mac install, most of these skip cleanly — the Tier 1 / Tier 2 suites
still validate the code paths that matter most.

Example from `quartus-mcp`:

```python
from vibe4fpga_platform import find_tool

@pytest.mark.skipif(
    not find_tool("quartus_sh", env_var="QUARTUS_SH"),
    reason="Quartus absent on runner",
)
async def test_quartus_environment(...): ...
```

## Cross-platform rules of thumb

When writing new code, lean on `vibe4fpga-platform` so the same source
runs on both OSes. A few patterns the shared helpers specifically solve:

| Instead of | Use | Why |
| --- | --- | --- |
| `"/tmp/foo.vvp"` | `scratch_file(".vvp")` | Windows has no `/tmp` |
| `tempfile.NamedTemporaryFile` | `scratch_file` / `scratch_dir` | the stdlib version deletes the file on GC; scratch_dir registers a single at-exit cleanup |
| `shutil.which("vivado")` | `find_tool("vivado", env_var="VIVADO_ROOT", extra_paths=[...])` | handles Windows `PATHEXT` + vendor install dir hints |
| `asyncio.create_subprocess_exec(...)` | `await platform.run(cmd, timeout=...)` | forces UTF-8 in child env; adds `\\?\` prefix on long Windows paths; surfaces timeouts as a typed error |
| string path concat | `Path(a) / b` | avoids the `/` vs `\` separator hazard |

The `rg '"/tmp' packages/` CI gate will catch the first of those; the
rest fail silently on Windows and pass on Mac, so self-discipline is the
primary defence.

## Running the Windows CI locally (via act)

If you want to smoke-test a PR before pushing, [act](https://github.com/nektos/act)
runs GitHub Actions jobs in local containers:

```bash
brew install act
act -j python-test -P windows-latest=windows-latest   # skips — act doesn't emulate Windows on Mac
act -j python-test -P macos-latest=-self-hosted       # runs the macOS leg
```

Full Windows coverage still requires GitHub's hosted runners — there's
no practical way to test Win-specific `\\?\` long paths or GBK console
behaviour from Mac.

## Editor setup

VS Code or Cursor with:

* **Python extension** (Microsoft / Anysphere)
* **Ruff extension** — we lint with ruff; run `uv run ruff check` per
  package before committing
* **rust-analyzer** for the one Rust crate

Each Python package has its own pyproject + uv venv under `.venv/`;
configure your editor to pick the MCP's venv when you open files in that
MCP's subtree (most extensions do this via `python.venvPath` or per-folder
settings).

## Debugging an MCP over stdio

`vibe4fpga-mcp-testkit` spawns the binary in-process so a single pytest
can test a full handshake + tool call:

```python
import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio

async def test_round_trip():
    async with stdio_server_spawn("fpga-project-mcp") as client:
        tools = await client.list_tools_names()
        result = await client.call_tool("scan_project", {"project_path": "..."})
        assert not result.isError
```

For interactive debugging, run the MCP directly and pipe in JSON-RPC
lines:

```bash
cd packages/mcp-servers/fpga-project-mcp
echo '{"jsonrpc":"2.0","id":1,"method":"initialize",...}' | uv run fpga-project-mcp
```

Or use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
for a GUI over stdio:

```bash
npx @modelcontextprotocol/inspector fpga-project-mcp
```
