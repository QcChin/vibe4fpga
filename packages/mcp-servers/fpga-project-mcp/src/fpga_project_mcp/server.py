"""fpga-project-mcp — Project indexing + 3 LLM-backed skills.

Exposes:
    Static project tools (no LLM required):
        scan_project, get_hierarchy, search_signal_tool,
        analyze_naming_conventions_tool, get_interface_neighbors,
        find_similar_modules
    LLM-backed skills (require llm-client env setup):
        spec_to_rtl          — spec2rtl pipeline (5 stages, optional repair loop)
        review_rtl           — code_review (FPGA pitfall checklist)
        suggest_timing_fix   — timing_fix (Vivado report → actionable fixes)
"""

from __future__ import annotations

import re

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .scanner import (
    analyze_naming_conventions,
    build_hierarchy,
    scan,
    search_signal,
    to_dict,
)
from .skills.code_review.checker import review_code
from .skills.spec2rtl.pipeline import run as spec2rtl_run
from .skills.timing_fix.skill import run as timing_fix_run

mcp = FastMCP("fpga-project-mcp")


# ═════════════════════════════════════════════════════════════════════════════
# Static project tools — scanner-only, no LLM.
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def scan_project(project_path: str) -> dict:
    """Scan RTL file tree and build module dependency graph.

    Args:
        project_path: Absolute path to the FPGA project root directory.

    Returns:
        JSON with file_count, module_count, and per-module details
        (file, line, signals, instantiates).
    """
    return to_dict(scan(project_path))


@mcp.tool()
async def get_hierarchy(project_path: str, top_module: str | None = None) -> dict:
    """Return module hierarchy as a nested tree JSON.

    Args:
        project_path: Absolute path to the project root.
        top_module:   Name of the top-level module (auto-detected if None).
    """
    return build_hierarchy(scan(project_path), top_module)


@mcp.tool()
async def search_signal_tool(project_path: str, signal_name: str) -> list[dict]:
    """Cross-file search for signal definitions and references.

    Returns:
        List of {file, line, text} for every occurrence of signal_name.
    """
    return search_signal(scan(project_path), signal_name)


@mcp.tool()
async def analyze_naming_conventions_tool(project_path: str) -> dict:
    """Statistical analysis of project naming conventions (confidence scoring).

    Only returns conventions where confidence > 0.70 to avoid applying
    outdated legacy patterns as mandatory standards.
    """
    return analyze_naming_conventions(scan(project_path))


@mcp.tool()
async def get_interface_neighbors(project_path: str, module_name: str) -> dict:
    """Query parent (upstream) and child (downstream) module interfaces.

    Used by Spec2RTL context injection to align port names with project
    conventions.

    Returns:
        {
            "parents":  [{"module": str, "file": str, "line": int}],
            "children": [{"module": str, "file": str, "line": int}],
        }
    """
    result  = scan(project_path)
    modules = result.modules

    parents = [
        {"module": name, "file": info.file_path, "line": info.line}
        for name, info in modules.items()
        if module_name in info.instantiates
    ]
    children: list[dict] = []
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
    description:  str,
    top_k:        int = 3,
) -> list[dict]:
    """Find modules similar to the given description (keyword match fallback).

    Full vector similarity search will be added in Phase 2 (RAG integration).
    Current implementation: keyword-based matching against module names and signals.
    """
    result   = scan(project_path)
    keywords = set(description.lower().split())

    scored: list[tuple[float, dict]] = []
    for name, info in result.modules.items():
        # Candidate-word bag = module name tokens ∪ first-20 signal names (lowercased).
        candidate_words = {
            w for w in re.split(r"[_\W]+", name.lower()) if w
        } | {s.lower() for s in info.signals[:20]}
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


# ═════════════════════════════════════════════════════════════════════════════
# LLM-backed skills — wrap pipeline modules behind pydantic-typed tools.
# ═════════════════════════════════════════════════════════════════════════════

class SpecToRtlInput(BaseModel):
    """Inputs for ``spec_to_rtl``."""

    spec:              str        = Field(..., description="Natural-language design specification (English or Chinese).")
    model:             str        = Field("claude", description="LLM model key (see vibe4fpga-llm-client registry).")
    project_path:      str | None = Field(None, description="Optional project root. When supplied, stage-3 injects local naming conventions into the RTL generator prompt.")
    max_repair_rounds: int        = Field(2, ge=0, le=5, description="Max stage 4→5 repair iterations when self-check FAILs.")


class ReviewRtlInput(BaseModel):
    """Inputs for ``review_rtl``."""

    rtl_code:  str = Field(..., description="Verilog/SystemVerilog source to review.")
    file_name: str = Field("unknown.v", description="File name for report labelling (not read from disk).")
    model:     str = Field("claude", description="LLM model key.")


class SuggestTimingFixInput(BaseModel):
    """Inputs for ``suggest_timing_fix``."""

    timing_report: str           = Field(..., description="Raw Vivado timing summary report text.")
    wns:           float | None  = Field(None, description="Worst Negative Slack (ns). Auto-extracted from the report if omitted.")
    tns:           float | None  = Field(None, description="Total Negative Slack (ns). Auto-extracted from the report if omitted.")
    rtl_context:   str           = Field("",   description="Optional RTL snippets for the critical-path signals; capped to 2000 chars.")
    model:         str           = Field("claude", description="LLM model key.")


@mcp.tool()
async def spec_to_rtl(inputs: SpecToRtlInput) -> dict:
    """Convert a natural-language FPGA spec into synthesizable Verilog.

    Runs the 5-stage Spec2RTL pipeline: spec parser → ambiguity detection
    → local context injection (naming conventions) → RTL generator → self-check
    → optional repair loop.

    Returns:
        Full ``Spec2RTLResult`` dict including generated RTL, self-check list,
        verification score (0-100), and the audit trail of autonomous decisions.
    """
    result = await spec2rtl_run(
        spec=inputs.spec,
        model=inputs.model,
        project_path=inputs.project_path,
        max_repair_rounds=inputs.max_repair_rounds,
    )
    return result.model_dump()


@mcp.tool()
async def review_rtl(inputs: ReviewRtlInput) -> dict:
    """Review Verilog/SystemVerilog for 10 FPGA-specific pitfalls.

    Checks latch inference, CDC violations, multi-driver bugs, sensitivity
    list completeness, signed/unsigned mixing, blocking/non-blocking misuse,
    reset coverage, dangling ports, and non-synthesizable ``initial`` blocks.

    Returns:
        ``{findings: [...], error_count, warning_count, summary}``.
    """
    return await review_code(
        rtl_code=inputs.rtl_code,
        file_name=inputs.file_name,
        model=inputs.model,
    )


@mcp.tool()
async def suggest_timing_fix(inputs: SuggestTimingFixInput) -> dict:
    """Parse a Vivado timing report and propose actionable fixes per critical path.

    Chooses between three strategies — pipeline register / multi-cycle path /
    logic restructuring — with tradeoff notes and ready-to-paste RTL or XDC
    snippets.

    Returns:
        ``{violations_found, wns, tns, fixes: [...], summary}``.
    """
    return await timing_fix_run(
        timing_report=inputs.timing_report,
        wns=inputs.wns,
        tns=inputs.tns,
        rtl_context=inputs.rtl_context,
        model=inputs.model,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Console-script entry point for ``fpga-project-mcp``."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
