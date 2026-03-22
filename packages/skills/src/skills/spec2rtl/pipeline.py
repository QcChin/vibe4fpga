"""Spec2RTL — 5-stage pipeline: natural language spec → synthesizable RTL.

Stage 1: Spec Parser         — NL → DesignIntent JSON
Stage 2: Ambiguity Detector  — BLOCKING (pause) vs ADVISORY (decide + record)
Stage 3: Context Injector    — naming conventions + similar modules (≤1000 tokens)
Stage 4: RTL Generator       — DesignIntent + context → Verilog (30+ rules, temp=0.1)
Stage 5: Self-Check          — second LLM call audits result, triggers repair loop

Design principle: "Autonomous decisions, but no hidden decisions."
Every engineering choice is stated in code comments and in the final report.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .models import (
    AmbiguityItem,
    AmbiguityLevel,
    DesignIntent,
    SelfCheckResult,
    Spec2RTLResult,
    TimingConstraint,
)
from .prompts import (
    AMBIGUITY_DETECTOR_SYSTEM,
    AMBIGUITY_DETECTOR_USER,
    RTL_GENERATOR_SYSTEM,
    RTL_GENERATOR_USER,
    SELF_CHECK_SYSTEM,
    SELF_CHECK_USER,
    SPEC_PARSER_SYSTEM,
    SPEC_PARSER_USER,
)


# ── LLM Router client ─────────────────────────────────────────────────────────

async def _llm_call(
    messages: list[dict],
    system: str,
    router_url: str,
    model: str = "claude",
    temperature: float = 0.3,
) -> str:
    """Call the LLM Router and return the full response text."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{router_url}/chat",
            json={
                "messages":    messages,
                "system":      system,
                "model":       model,
                "temperature": temperature,
                "stream":      False,
            },
        )
        resp.raise_for_status()
        return resp.json()["content"]


def _parse_json(text: str) -> Any:
    """Extract and parse JSON from LLM response (handles markdown fences)."""
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


# ── Stage 1: Spec Parser ──────────────────────────────────────────────────────

async def stage1_parse_spec(spec: str, router_url: str, model: str) -> DesignIntent:
    """Decompose natural language spec into structured DesignIntent."""
    raw = await _llm_call(
        messages=[{"role": "user", "content": SPEC_PARSER_USER.format(spec=spec)}],
        system=SPEC_PARSER_SYSTEM,
        router_url=router_url,
        model=model,
        temperature=0.1,
    )
    data = _parse_json(raw)
    intent = DesignIntent(**data)
    intent.raw_spec = spec
    return intent


# ── Stage 2: Ambiguity Detector ───────────────────────────────────────────────

async def stage2_detect_ambiguities(
    intent: DesignIntent,
    spec: str,
    router_url: str,
    model: str,
) -> list[AmbiguityItem]:
    """Identify BLOCKING (must ask) and ADVISORY (autonomous decision) ambiguities."""
    raw = await _llm_call(
        messages=[{
            "role": "user",
            "content": AMBIGUITY_DETECTOR_USER.format(
                design_intent_json=intent.model_dump_json(indent=2),
                spec=spec,
            ),
        }],
        system=AMBIGUITY_DETECTOR_SYSTEM,
        router_url=router_url,
        model=model,
        temperature=0.1,
    )
    items_data = _parse_json(raw)
    return [AmbiguityItem(**item) for item in items_data]


# ── Stage 3: Context Injector ─────────────────────────────────────────────────

async def stage3_inject_context(
    intent: DesignIntent,
    project_path: str | None,
    fpga_project_mcp_url: str | None,
) -> str:
    """Fetch naming conventions and similar module skeletons from fpga-project-mcp.

    Returns a context string for the RTL generator (≤1000 tokens).
    Returns empty string if no project path is available.
    """
    if not project_path or not fpga_project_mcp_url:
        return ""

    context_parts: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Naming conventions
            resp = await client.post(
                f"{fpga_project_mcp_url}/tools/analyze_naming_conventions_tool",
                json={"project_path": project_path},
            )
            if resp.status_code == 200:
                conventions = resp.json()
                if conventions:
                    context_parts.append(
                        "Project naming conventions:\n"
                        + json.dumps(conventions, indent=2)
                    )
    except Exception:
        pass  # Context injection is best-effort

    return "\n\n".join(context_parts)[:3000]  # cap to ~750 tokens


# ── Stage 4: RTL Generator ────────────────────────────────────────────────────

async def stage4_generate_rtl(
    intent: DesignIntent,
    declared_decisions: list[str],
    context: str,
    router_url: str,
    model: str,
) -> str:
    """Generate synthesizable RTL from design intent (temperature=0.1)."""
    decisions_text = "\n".join(f"- {d}" for d in declared_decisions) if declared_decisions else "None"

    raw = await _llm_call(
        messages=[{
            "role": "user",
            "content": RTL_GENERATOR_USER.format(
                design_intent_json=intent.model_dump_json(indent=2),
                declared_decisions=decisions_text,
            ),
        }],
        system=RTL_GENERATOR_SYSTEM.format(context=context or "No project context available."),
        router_url=router_url,
        model=model,
        temperature=0.1,   # low temperature for deterministic, rule-following code
    )

    # Strip accidental markdown fences
    raw = re.sub(r"^```(?:verilog|systemverilog|sv)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
    return raw.strip()


# ── Stage 5: Self-Check ───────────────────────────────────────────────────────

async def stage5_self_check(
    spec: str,
    intent: DesignIntent,
    rtl_code: str,
    router_url: str,
    model: str,
) -> list[SelfCheckResult]:
    """Second independent LLM call audits the generated RTL."""
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
    items_data = _parse_json(raw)
    return [SelfCheckResult(**item) for item in items_data]


def _compute_score(checks: list[SelfCheckResult]) -> float:
    """Compute verification score from self-check results."""
    score = 100.0
    for check in checks:
        if check.status == "FAIL":
            score -= 12.0
    return max(0.0, score)


# ── Main pipeline entry point ─────────────────────────────────────────────────

async def run(
    spec: str,
    router_url: str = "http://localhost:8765",
    model: str = "claude",
    project_path: str | None = None,
    fpga_project_mcp_url: str | None = None,
    max_repair_rounds: int = 2,
) -> Spec2RTLResult:
    """Run the full 5-stage Spec2RTL pipeline.

    Args:
        spec:                  Natural language design specification.
        router_url:            LLM Router URL.
        model:                 LLM backend key ("claude" | "ollama").
        project_path:          Optional project root for context injection.
        fpga_project_mcp_url:  Optional fpga-project-mcp endpoint for context.
        max_repair_rounds:     Max auto-repair iterations on FAIL checks.

    Returns:
        Spec2RTLResult with RTL code, score, and audit trail.
    """
    # Stage 1
    intent = await stage1_parse_spec(spec, router_url, model)

    # Stage 2
    ambiguities = await stage2_detect_ambiguities(intent, spec, router_url, model)
    blocking = [a for a in ambiguities if a.level == AmbiguityLevel.BLOCKING]
    advisory = [a for a in ambiguities if a.level == AmbiguityLevel.ADVISORY]

    # Collect declared decisions from ADVISORY items
    declared_decisions = [
        f"{a.question} → {a.autonomous_decision}"
        for a in advisory
        if a.autonomous_decision
    ]

    # If BLOCKING ambiguities exist, return them for engineer to resolve
    if blocking:
        return Spec2RTLResult(
            module_name=intent.module_name,
            rtl_code="",
            design_intent=intent,
            ambiguities_resolved=ambiguities,
            self_check=[SelfCheckResult(
                item="blocking_ambiguities",
                status="FAIL",
                note="; ".join(a.question for a in blocking),
            )],
            score=0.0,
            passed=False,
            declared_decisions=declared_decisions,
        )

    # Stage 3
    context = await stage3_inject_context(intent, project_path, fpga_project_mcp_url)

    # Stage 4 + 5 with repair loop
    rtl_code = ""
    checks: list[SelfCheckResult] = []

    for round_no in range(max_repair_rounds + 1):
        rtl_code = await stage4_generate_rtl(
            intent, declared_decisions, context, router_url, model
        )
        checks = await stage5_self_check(spec, intent, rtl_code, router_url, model)

        failed = [c for c in checks if c.status == "FAIL"]
        if not failed or round_no == max_repair_rounds:
            break

        # Repair: append failure context to declared decisions and regenerate
        declared_decisions.append(
            f"[Repair round {round_no + 1}] Fix these issues: "
            + "; ".join(f.note for f in failed)
        )

    score = _compute_score(checks)

    return Spec2RTLResult(
        module_name=intent.module_name,
        rtl_code=rtl_code,
        design_intent=intent,
        ambiguities_resolved=ambiguities,
        self_check=checks,
        score=score,
        passed=score >= 60.0,
        declared_decisions=declared_decisions,
    )
