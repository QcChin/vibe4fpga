"""Five-layer RTL verification pipeline orchestrator.

Layer | Type              | Time         | Tools
------+-------------------+--------------+------------------------
  1   | Static Check      | millisecond  | verilator + verible
  2   | Functional Sim    | minute       | Icarus / Verilator
  3   | Formal Verify     | key props    | SymbiYosys (optional)
  4   | Synthesis+Timing  | 10+ minutes  | Vivado
  5   | Spec Compliance   | seconds      | LLM self-check

Layered progressive design: cheapest checks run first, failures inject
structured error info back to LLM to trigger auto-repair before the next layer.
"""

from __future__ import annotations

import asyncio

import httpx

from .reporter import generate_report
from .scorer import compute_score

EDA_MCP_URL     = "http://localhost:8766"   # eda-bridge-mcp default
ROUTER_URL      = "http://localhost:8765"


async def _call_eda(
    endpoint: str,
    payload: dict,
    eda_mcp_url: str = EDA_MCP_URL,
) -> dict:
    """Call an eda-bridge-mcp tool endpoint."""
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{eda_mcp_url}/tools/{endpoint}", json=payload)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}


async def layer1_lint(
    files: list[str],
    project_path: str,
    eda_mcp_url: str = EDA_MCP_URL,
) -> dict:
    """Layer 1: Static lint (verilator + verible). ~12ms/file."""
    result = await _call_eda("run_lint", {"project_path": project_path, "files": files}, eda_mcp_url)
    return {
        **result,
        "summary": (
            f"Lint: {result.get('error_count', 0)} error(s), "
            f"{result.get('warning_count', 0)} warning(s)"
        ),
    }


async def layer2_simulation(
    testbench: str,
    source_files: list[str],
    project_path: str,
    simulator: str = "icarus",
    eda_mcp_url: str = EDA_MCP_URL,
) -> dict:
    """Layer 2: Functional simulation (Icarus/Verilator/xsim)."""
    result = await _call_eda(
        "run_simulation",
        {
            "project_path": project_path,
            "testbench":     testbench,
            "source_files":  source_files,
            "simulator":     simulator,
        },
        eda_mcp_url,
    )
    return {
        **result,
        "failed_scenarios": 0 if result.get("success") else 1,
        "summary": "Simulation: PASSED" if result.get("success") else "Simulation: FAILED",
    }


async def layer3_formal(
    rtl_files: list[str],
    project_path: str,
    eda_mcp_url: str = EDA_MCP_URL,
) -> dict:
    """Layer 3: Formal verification via SymbiYosys (optional)."""
    import shutil
    if not shutil.which("sby"):
        return {
            "status":  "skipped",
            "reason":  "SymbiYosys (sby) not found — install from https://github.com/YosysHQ/oss-cad-suite",
            "summary": "Formal: SKIPPED (sby not installed)",
            "property_failures": 0,
        }

    # Build a basic sby config for SVA properties in `ifdef FORMAL blocks
    sby_script = "\n".join([
        "[options]",
        "mode prove",
        "depth 20",
        "",
        "[engines]",
        "smtbmc",
        "",
        "[script]",
        *[f"read -formal {f}" for f in rtl_files],
        "prep -top $(top_module)",
        "",
        "[files]",
        *rtl_files,
    ])

    result = await _call_eda(
        "run_synthesis",   # placeholder — sby needs its own endpoint (Phase 4)
        {"project_path": project_path, "files": rtl_files, "top_module": "unknown"},
        eda_mcp_url,
    )
    return {
        **result,
        "property_failures": 0,
        "summary": "Formal: stub (full SymbiYosys integration in Phase 4)",
    }


async def layer4_synthesis(
    files: list[str],
    top_module: str,
    project_path: str,
    part: str = "xc7a35tcpg236-1",
    eda_mcp_url: str = EDA_MCP_URL,
) -> dict:
    """Layer 4: Vivado synthesis + timing analysis."""
    result = await _call_eda(
        "run_synthesis",
        {
            "project_path": project_path,
            "files":        files,
            "top_module":   top_module,
            "part":         part,
        },
        eda_mcp_url,
    )

    timing = result.get("timing", {})
    wns    = timing.get("wns", 0)
    return {
        **result,
        "timing_violations": 1 if (wns is not None and wns < 0) else 0,
        "summary": (
            f"Synthesis: {'PASSED' if result.get('success') else 'FAILED'} | "
            f"WNS={wns} ns"
        ),
    }


async def layer5_spec_compliance(
    spec: str,
    rtl_code: str,
    router_url: str = ROUTER_URL,
    model: str = "claude",
) -> dict:
    """Layer 5: Spec compliance via LLM self-check (independent second call)."""
    from skills.spec2rtl.models import DesignIntent
    from skills.spec2rtl.pipeline import _llm_call
    from skills.spec2rtl.prompts import SELF_CHECK_SYSTEM, SELF_CHECK_USER

    # Use a minimal DesignIntent to satisfy the prompt template
    intent = DesignIntent(module_name="unknown", description=spec, raw_spec=spec)

    raw = await _llm_call(
        messages=[{
            "role": "user",
            "content": SELF_CHECK_USER.format(
                spec=spec,
                design_intent_json=intent.model_dump_json(indent=2),
                rtl_code=rtl_code,
            ),
        }],
        system=SELF_CHECK_SYSTEM,
        router_url=router_url,
        model=model,
        temperature=0.1,
    )

    import json, re
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
    try:
        checks = json.loads(raw.strip())
    except json.JSONDecodeError:
        checks = []

    failed = sum(1 for c in checks if c.get("status") == "FAIL")
    declared = sum(1 for c in checks if c.get("status") == "DECLARED")

    return {
        "checks":          checks,
        "failed_clauses":  failed,
        "declared_clauses": declared,
        "summary": f"Spec compliance: {failed} failed, {declared} declared",
    }


# ── Main pipeline entry point ─────────────────────────────────────────────────

async def run(
    files: list[str],
    top_module: str,
    spec: str,
    rtl_code: str,
    project_path: str,
    testbench: str | None = None,
    part: str = "xc7a35tcpg236-1",
    simulator: str = "icarus",
    router_url: str = ROUTER_URL,
    model: str = "claude",
    eda_mcp_url: str = EDA_MCP_URL,
    skip_layers: list[int] | None = None,
) -> dict:
    """Run the full 5-layer verification pipeline.

    Args:
        files:        RTL source files.
        top_module:   Top-level module name.
        spec:         Original natural language specification.
        rtl_code:     RTL code as string (for Layer 5).
        project_path: Project root directory.
        testbench:    Testbench file path (Layer 2). If None, Layer 2 is skipped.
        part:         FPGA part number (Layer 4).
        skip_layers:  List of layer numbers to skip (e.g. [4] to skip synthesis).

    Returns:
        Full verification result with score, verdict, and Markdown report.
    """
    skip = set(skip_layers or [])
    layer_results: dict[str, dict] = {}

    # Layers 1 and 5 are fast; run sequentially for dependency
    # Layers 2, 3, 4 can run in parallel (but 4 depends on having sources)

    # ── Layer 1: Lint (always run first) ─────────────────────────────────────
    if 1 not in skip:
        layer_results["lint"] = await layer1_lint(files, project_path, eda_mcp_url)
        lint_errors = layer_results["lint"].get("error_count", 0)
        if lint_errors > 5:
            # Too many lint errors — stop early
            layer_results["lint"]["early_stop"] = True
            score_bd = compute_score(lint_results=layer_results["lint"])
            layer_results["report"] = await generate_report(layer_results, score_bd.to_dict(), router_url, model)
            return {**layer_results, **score_bd.to_dict(), "stopped_at_layer": 1}

    # ── Layers 2, 3, 4 in parallel (if prerequisites met) ────────────────────
    parallel_tasks: dict[str, object] = {}

    if 2 not in skip and testbench:
        parallel_tasks["sim"] = layer2_simulation(
            testbench, files, project_path, simulator, eda_mcp_url
        )
    if 3 not in skip:
        parallel_tasks["formal"] = layer3_formal(files, project_path, eda_mcp_url)
    if 4 not in skip:
        parallel_tasks["synthesis"] = layer4_synthesis(
            files, top_module, project_path, part, eda_mcp_url
        )

    if parallel_tasks:
        task_names = list(parallel_tasks.keys())
        task_coros = [parallel_tasks[n] for n in task_names]
        results = await asyncio.gather(*task_coros, return_exceptions=True)
        for name, result in zip(task_names, results):
            if isinstance(result, Exception):
                layer_results[name] = {"error": str(result), "summary": f"Layer {name}: ERROR"}
            else:
                layer_results[name] = result

    # ── Layer 5: Spec compliance ──────────────────────────────────────────────
    if 5 not in skip and rtl_code:
        layer_results["spec"] = await layer5_spec_compliance(
            spec, rtl_code, router_url, model
        )

    # ── Score and report ──────────────────────────────────────────────────────
    score_bd = compute_score(
        lint_results       = layer_results.get("lint"),
        sim_results        = layer_results.get("sim"),
        formal_results     = layer_results.get("formal"),
        synthesis_results  = layer_results.get("synthesis"),
        spec_check_results = layer_results.get("spec", {}).get("checks"),
    )

    report_md = await generate_report(layer_results, score_bd.to_dict(), router_url, model)

    return {
        "layers":     layer_results,
        "report_md":  report_md,
        **score_bd.to_dict(),
    }
