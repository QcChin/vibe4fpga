# vibe4fpga-mcp-testkit

**Dev-only**, not published. Every MCP server in the monorepo uses this package
in its `tests/` directory to run Tier 1 smoke tests (stdio handshake +
`list_tools`) with minimal boilerplate.

## Use

```python
# packages/mcp-servers/<name>/tests/test_smoke.py
import pytest
from vibe4fpga_mcp_testkit import stdio_server_spawn

pytestmark = pytest.mark.asyncio

async def test_tool_list():
    async with stdio_server_spawn("fpga-project-mcp") as client:
        names = await client.list_tools_names()
        assert {"scan_project", "get_hierarchy"} <= set(names)

async def test_ping():
    async with stdio_server_spawn("fpga-project-mcp") as client:
        assert client.server_name  # handshake succeeded
```

## Install

Path-dep in each MCP's `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "vibe4fpga-mcp-testkit",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.uv.sources]
vibe4fpga-mcp-testkit = { path = "../../shared/mcp-testkit", editable = true }
```
