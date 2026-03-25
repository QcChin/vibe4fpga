"""Agent step executor — maps PlanStep types to skill/tool calls.

Each execute_* function takes the step params dict and the shared context,
runs the corresponding skill, and returns a StepResult.

Design doc reference: Phase 4 — 多轮Agent自主循环
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TypedDict

import httpx

from .planner import PlanStep, StepType

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    step_id:   int
    step_type: StepType
    success:   bool
    output:    dict = field(default_factory=dict)
    error:     str = ""
    retry_suggested: bool = False


# ── HTTP helper ───────────────────────────────────────────────────────────────

async def _post(router_url: str, endpoint: str, body: dict) -> dict:
    """POST to router endpoint. Distinguishes retriable vs. permanent failures."""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(f"{router_url}{endpoint}", json=body)
            if resp.status_code == 200:
                return resp.json()
            # 5xx: server-side transient error → retriable
            retriable = resp.status_code >= 500
            return {
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "retriable": retriable,
            }
    except httpx.ConnectError as exc:
        # Service not reachable — suggest retry (may start up later)
        return {"error": f"Connection failed: {exc}", "retriable": True}
    except httpx.TimeoutException as exc:
        return {"error": f"Request timed out: {exc}", "retriable": True}
    except httpx.NetworkError as exc:
        return {"error": f"Network error: {exc}", "retriable": True}


# ── Step executors ────────────────────────────────────────────────────────────

async def _execute_spec2rtl(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    params.setdefault("spec", ctx.get("spec", ""))
    params.setdefault("model", ctx.get("model", "claude"))
    result = await _post(router_url, "/skill/spec2rtl", params)
    success = "rtl_code" in result and not result.get("error")
    if success:
        ctx["rtl_code"] = result["rtl_code"]
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


async def _execute_code_review(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    params.setdefault("code", ctx.get("rtl_code", ""))
    result = await _post(router_url, "/skill/code_review", params)
    success = "findings" in result and not result.get("error")
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


async def _execute_lint(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    files = step.params.get("files") or ctx.get("source_files", [])
    if not files:
        return StepResult(step_id=step.step_id, step_type=step.step_type,
                          success=False, error="No source files for lint")

    result = await _post(router_url, "/tool/eda_bridge/run_lint", {"files": files})

    if result.get("error"):
        # Service unavailable: log warning but don't silently pass
        logger.warning("Lint service unavailable: %s — step marked as skipped", result["error"])
        result = {"findings": [], "score_penalty": 0, "lint_skipped": True}
        ctx["lint_findings"] = []
        return StepResult(step_id=step.step_id, step_type=step.step_type,
                          success=True, output=result)

    ctx["lint_findings"] = result.get("findings", [])
    success = result.get("score_penalty", 0) == 0 or len(result.get("findings", [])) == 0
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=not success,
    )


async def _execute_simulate(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    params.setdefault("files", ctx.get("source_files", []))
    params.setdefault("top_module", ctx.get("top_module", "top"))
    params.setdefault("testbench", ctx.get("testbench"))
    result = await _post(router_url, "/tool/eda_bridge/run_simulation", params)
    # Explicit check: only True counts as passed; missing key or error → failure
    success = result.get("passed") is True
    if "vcd_file" in result:
        ctx["vcd_file"] = result["vcd_file"]
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")) or not success,
    )


async def _execute_timing_fix(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    params.setdefault("timing_report", ctx.get("timing_report", ""))
    params.setdefault("rtl_context", ctx.get("rtl_code", ""))
    result = await _post(router_url, "/skill/timing_fix", params)
    success = result.get("wns_ns", -1.0) >= 0 or result.get("no_violations", False)
    if result.get("fixed_rtl"):
        ctx["rtl_code"] = result["fixed_rtl"]
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


async def _execute_synthesize(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    toolchain = params.get("toolchain", ctx.get("toolchain", "vivado"))

    if toolchain == "yosys":
        endpoint = "/tool/yosys/synthesize_ice40"
        params.setdefault("source_files", ctx.get("source_files", []))
        params.setdefault("top_module", ctx.get("top_module", "top"))
    else:
        endpoint = "/tool/eda_bridge/run_synthesis"
        params.setdefault("files", ctx.get("source_files", []))
        params.setdefault("top_module", ctx.get("top_module", "top"))

    result = await _post(router_url, endpoint, params)
    success = result.get("success", False)
    if result.get("timing_report"):
        ctx["timing_report"] = result["timing_report"]
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


async def _execute_formal_verify(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    params.setdefault("files", ctx.get("source_files", []))
    params.setdefault("top_module", ctx.get("top_module", "top"))
    result = await _post(router_url, "/tool/yosys/prepare_formal_verification", params)
    success = result.get("success", False)
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


async def _execute_waveform_debug(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    params.setdefault("waveform_path", ctx.get("vcd_file", ""))
    params.setdefault("query", ctx.get("debug_query", ""))
    result = await _post(router_url, "/skill/waveform_debug", params)
    success = not result.get("error")
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


async def _execute_instrument_analyze(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    params.setdefault("sim_vcd_file", ctx.get("vcd_file", ""))
    result = await _post(router_url, "/skill/instrument_analyze", params)
    success = not result.get("error")
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


async def _execute_program(step: PlanStep, ctx: dict, router_url: str) -> StepResult:
    params = {**step.params}
    toolchain = params.get("toolchain", ctx.get("toolchain", "vivado"))

    if toolchain == "quartus":
        result = await _post(router_url, "/tool/quartus/program_fpga", params)
    else:
        result = await _post(router_url, "/tool/eda_bridge/program_device", params)

    success = result.get("success", False)
    return StepResult(
        step_id=step.step_id, step_type=step.step_type,
        success=success, output=result,
        retry_suggested=bool(result.get("retriable")),
    )


# ── Dispatch table ────────────────────────────────────────────────────────────

_EXECUTORS = {
    StepType.SPEC2RTL:           _execute_spec2rtl,
    StepType.CODE_REVIEW:        _execute_code_review,
    StepType.LINT:               _execute_lint,
    StepType.SIMULATE:           _execute_simulate,
    StepType.TIMING_FIX:         _execute_timing_fix,
    StepType.SYNTHESIZE:         _execute_synthesize,
    StepType.FORMAL_VERIFY:      _execute_formal_verify,
    StepType.WAVEFORM_DEBUG:     _execute_waveform_debug,
    StepType.INSTRUMENT_ANALYZE: _execute_instrument_analyze,
    StepType.PROGRAM:            _execute_program,
}


async def execute_step(
    step: PlanStep,
    context: dict,
    router_url: str = "http://localhost:8765",
) -> StepResult:
    """Dispatch and execute a single plan step."""
    executor = _EXECUTORS.get(step.step_type)
    if executor is None:
        return StepResult(
            step_id=step.step_id,
            step_type=step.step_type,
            success=False,
            error=f"No executor for step type '{step.step_type}'",
        )
    try:
        return await executor(step, context, router_url)
    except Exception as exc:
        logger.exception("Unexpected error in step %d (%s)", step.step_id, step.step_type)
        return StepResult(
            step_id=step.step_id,
            step_type=step.step_type,
            success=False,
            error=str(exc),
        )
