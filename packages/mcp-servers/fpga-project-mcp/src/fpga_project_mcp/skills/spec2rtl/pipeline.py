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

from .._llm import call_llm, parse_json_response
from .models import (
    AmbiguityItem,
    AmbiguityLevel,
    DesignIntent,
    SelfCheckResult,
    Spec2RTLResult,
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


# ── Stage 1: Spec Parser ──────────────────────────────────────────────────────

async def stage1_parse_spec(spec: str, model: str) -> DesignIntent:
    """Decompose natural language spec into structured DesignIntent."""
    raw = await call_llm(
        messages=[{"role": "user", "content": SPEC_PARSER_USER.format(spec=spec)}],
        system=SPEC_PARSER_SYSTEM,
        model=model,
        temperature=0.1,
    )
    intent = DesignIntent(**parse_json_response(raw))
    intent.raw_spec = spec
    return intent


# ── Stage 2: Ambiguity Detector ───────────────────────────────────────────────

async def stage2_detect_ambiguities(
    intent: DesignIntent,
    spec:   str,
    model:  str,
) -> list[AmbiguityItem]:
    """Identify BLOCKING (must ask) and ADVISORY (autonomous decision) ambiguities."""
    raw = await call_llm(
        messages=[{
            "role": "user",
            "content": AMBIGUITY_DETECTOR_USER.format(
                design_intent_json=intent.model_dump_json(indent=2),
                spec=spec,
            ),
        }],
        system=AMBIGUITY_DETECTOR_SYSTEM,
        model=model,
        temperature=0.1,
    )
    return [AmbiguityItem(**item) for item in parse_json_response(raw)]


# ── Stage 3: Context Injector ─────────────────────────────────────────────────

async def stage3_inject_context(project_path: str | None) -> str:
    """Fetch naming conventions + module skeletons *from within this MCP*.

    In the pre-pivot architecture this called fpga-project-mcp over HTTP.
    We're *in* fpga-project-mcp now, so we call the scanner directly — saves a
    network round-trip and removes the last non-LLM HTTP dependency from this
    skill.

    Returns a context string capped at ~750 tokens for the RTL generator.
    Returns empty string when no project path is supplied.
    """
    if not project_path:
        return ""

    # Local imports to keep stage1/2/4/5 free of scanner deps at module load.
    from ...scanner import analyze_naming_conventions, scan

    parts: list[str] = []
    try:
        result = scan(project_path)
    except Exception:  # best-effort — a broken project should not kill the skill
        return ""

    conventions = analyze_naming_conventions(result)
    if conventions:
        parts.append("Project naming conventions:\n" + json.dumps(conventions, indent=2))

    return "\n\n".join(parts)[:3000]


# ── Stage 4: RTL Generator ────────────────────────────────────────────────────

async def stage4_generate_rtl(
    intent:             DesignIntent,
    declared_decisions: list[str],
    context:            str,
    model:              str,
) -> str:
    """Generate synthesizable RTL from design intent (temperature=0.1)."""
    decisions_text = "\n".join(f"- {d}" for d in declared_decisions) if declared_decisions else "None"

    raw = await call_llm(
        messages=[{
            "role": "user",
            "content": RTL_GENERATOR_USER.format(
                design_intent_json=intent.model_dump_json(indent=2),
                declared_decisions=decisions_text,
            ),
        }],
        system=RTL_GENERATOR_SYSTEM.format(context=context or "No project context available."),
        model=model,
        temperature=0.1,
    )

    # Strip accidental markdown fences.
    cleaned = re.sub(r"^```(?:verilog|systemverilog|sv)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned.strip(), flags=re.MULTILINE)
    return cleaned.strip()


# ── Stage 5: Self-Check ───────────────────────────────────────────────────────

async def stage5_self_check(
    spec:     str,
    intent:   DesignIntent,
    rtl_code: str,
    model:    str,
) -> list[SelfCheckResult]:
    """Second independent LLM call audits the generated RTL."""
    raw = await call_llm(
        messages=[{
            "role": "user",
            "content": SELF_CHECK_USER.format(
                spec=spec,
                design_intent_json=intent.model_dump_json(indent=2),
                rtl_code=rtl_code,
            ),
        }],
        system=SELF_CHECK_SYSTEM,
        model=model,
        temperature=0.1,
    )
    return [SelfCheckResult(**item) for item in parse_json_response(raw)]


def _compute_score(checks: list[SelfCheckResult]) -> float:
    """Compute verification score — 100 minus 12 per FAIL, floored at 0."""
    score = 100.0 - sum(12.0 for c in checks if c.status == "FAIL")
    return max(0.0, score)


# ── Main pipeline entry point ─────────────────────────────────────────────────

async def run(
    spec:              str,
    model:             str        = "claude",
    project_path:      str | None = None,
    max_repair_rounds: int        = 2,
) -> Spec2RTLResult:
    """Run the full 5-stage Spec2RTL pipeline.

    Args:
        spec:              Natural-language design specification.
        model:             LLM backend key (see vibe4fpga-llm-client registry).
        project_path:      Optional project root for context injection.
                           When supplied, stage 3 pulls naming conventions
                           from this MCP's scanner directly (no HTTP).
        max_repair_rounds: Upper bound on stage 4→5 repair iterations
                           triggered by FAIL self-checks.
    """
    # Stage 1.
    intent = await stage1_parse_spec(spec, model)

    # Stage 2.
    ambiguities = await stage2_detect_ambiguities(intent, spec, model)
    blocking  = [a for a in ambiguities if a.level == AmbiguityLevel.BLOCKING]
    advisory  = [a for a in ambiguities if a.level == AmbiguityLevel.ADVISORY]

    declared_decisions = [
        f"{a.question} → {a.autonomous_decision}"
        for a in advisory
        if a.autonomous_decision
    ]

    # BLOCKING ambiguities pause the pipeline and hand control back.
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

    # Stage 3 (in-process scanner call — no HTTP).
    context = await stage3_inject_context(project_path)

    # Stages 4 + 5 with a bounded repair loop.
    rtl_code = ""
    checks: list[SelfCheckResult] = []

    for round_no in range(max_repair_rounds + 1):
        rtl_code = await stage4_generate_rtl(intent, declared_decisions, context, model)
        checks   = await stage5_self_check(spec, intent, rtl_code, model)

        failed = [c for c in checks if c.status == "FAIL"]
        if not failed or round_no == max_repair_rounds:
            break

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
