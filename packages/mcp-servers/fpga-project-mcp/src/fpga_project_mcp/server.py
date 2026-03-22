"""fpga-project-mcp — Project file indexing and module dependency graph."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .scanner import (
    analyze_naming_conventions,
    build_hierarchy,
    scan,
    search_signal,
    to_dict,
)

mcp = FastMCP("fpga-project-mcp")


@mcp.tool()
async def scan_project(project_path: str) -> dict:
    """Scan RTL file tree and build module dependency graph.

    Args:
        project_path: Absolute path to the FPGA project root directory.

    Returns:
        JSON with file_count, module_count, and per-module details
        (file, line, signals, instantiates).
    """
    result = scan(project_path)
    return to_dict(result)


@mcp.tool()
async def get_hierarchy(project_path: str, top_module: str | None = None) -> dict:
    """Return module hierarchy as a nested tree JSON.

    Args:
        project_path: Absolute path to the project root.
        top_module:   Name of the top-level module (auto-detected if None).
    """
    result = scan(project_path)
    return build_hierarchy(result, top_module)


@mcp.tool()
async def search_signal_tool(project_path: str, signal_name: str) -> list[dict]:
    """Cross-file search for signal definitions and references.

    Returns:
        List of {file, line, text} for every occurrence of signal_name.
    """
    result = scan(project_path)
    return search_signal(result, signal_name)


@mcp.tool()
async def analyze_naming_conventions_tool(project_path: str) -> dict:
    """Statistical analysis of project naming conventions (confidence scoring).

    Only returns conventions where confidence > 0.70 to avoid applying
    outdated legacy patterns as mandatory standards.

    Returns:
        {
            "clock_signal":  {"pattern": "clk",  "confidence": 1.00},
            "reset_signal":  {"pattern": "rst_n", "confidence": 0.85},
            "module_prefix": {"pattern": "u_",   "confidence": 0.92},
            ...
        }
    """
    result = scan(project_path)
    return analyze_naming_conventions(result)


@mcp.tool()
async def get_interface_neighbors(project_path: str, module_name: str) -> dict:
    """Query parent (upstream) and child (downstream) module interfaces.

    Used by Spec2RTL context injection to align port names with project conventions.

    Returns:
        {
            "parents":  [{"module": str, "file": str, "line": int}],
            "children": [{"module": str, "file": str, "line": int}],
        }
    """
    result = scan(project_path)
    modules = result.modules

    parents = [
        {"module": name, "file": info.file_path, "line": info.line}
        for name, info in modules.items()
        if module_name in info.instantiates
    ]
    children = []
    if module_name in modules:
        for child_name in modules[module_name].instantiates:
            if child_name in modules:
                child = modules[child_name]
                children.append({
                    "module": child_name,
                    "file":   child.file_path,
                    "line":   child.line,
                })

    return {"parents": parents, "children": children}


@mcp.tool()
async def find_similar_modules(
    project_path: str,
    description: str,
    top_k: int = 3,
) -> list[dict]:
    """Find modules similar to the given description (keyword match fallback).

    Full vector similarity search will be added in Phase 2 (RAG integration).
    Current implementation: keyword-based matching against module names and signals.

    Args:
        description: Natural language description of desired functionality.
        top_k:       Number of results to return.
    """
    result = scan(project_path)
    keywords = set(description.lower().split())

    scored: list[tuple[float, dict]] = []
    for name, info in result.modules.items():
        # Score = keyword overlap with module name + signal names
        candidate_words = set(
            re.split(r"[_\W]+", name.lower())
            + [s.lower() for s in info.signals[:20]]
        )
        import re
        candidate_words = set(
            w for w in re.split(r"[_\W]+", name.lower()) if w
        ) | {s.lower() for s in info.signals[:20]}

        score = len(keywords & candidate_words) / max(len(keywords), 1)
        if score > 0:
            scored.append((score, {
                "module": name,
                "file":   info.file_path,
                "line":   info.line,
                "score":  round(score, 2),
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
