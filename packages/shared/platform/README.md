# vibe4fpga-platform

Cross-platform helpers used by every vibe4fpga MCP server. One place for scratch
paths, external-tool discovery, and async subprocess handling so bugs in any of
these three areas get fixed once.

## Why

* **No more `/tmp` hardcoded**: `scratch_file(".vvp")` works the same on
  macOS and Windows, auto-cleaned on process exit.
* **No more `shutil.which` surprises on Windows**: `find_tool()` honours
  PATHEXT, applies `.exe` fallback, and supports vendor install hints.
* **No more GBK-console garbled subprocess output**: `run()` forces
  `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` in the child env.
* **No more 260-char path failures**: Windows long paths get `\\?\` prefixed
  transparently when they exceed 240 chars.

## Use

```python
from vibe4fpga_platform import scratch_file, require_tool, run

vvp = scratch_file(".vvp")

iverilog = require_tool("iverilog")
result = await run([iverilog, "-g2012", "-o", vvp, *sources, testbench], timeout=120)
if not result.ok:
    raise RuntimeError(result.stderr_text())
```

## Install

```bash
uv pip install vibe4fpga-platform
```

No optional extras — the entire library is stdlib-based.

## Development

```bash
cd packages/shared/platform
uv sync --all-extras
uv run pytest
```
