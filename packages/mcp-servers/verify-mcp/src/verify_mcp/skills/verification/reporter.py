"""Verification report generator — LLM narrative synthesis.

The reporter aggregates data from all 5 verification stages and calls
the shared LLM client to produce a human-readable Markdown report with
actionable recommendations.
"""

from __future__ import annotations

import json

from .._llm import call_llm

REPORTER_SYSTEM = """\
You are an FPGA verification engineer writing a concise technical report.

Given verification results from 5 stages (Lint, Simulation, Formal, Synthesis, Spec),
write a Markdown report that:

1. Opens with overall verdict (PASS/REVIEW/FAIL) and numeric score
2. Has one section per stage with key findings (bullet points)
3. Ends with a prioritized "Action Items" list (most critical first)
4. Uses technical language appropriate for FPGA engineers
5. Calls out DECLARED items separately (autonomous decisions not yet confirmed)

Keep the report under 500 words. Be concise and actionable.
"""

REPORTER_PROMPT = """\
Verification Results:
```json
{results_json}
```

Score: {score}/100  |  Verdict: {verdict}

Score breakdown:
{breakdown_text}

Write the verification report.
"""


def _fallback_report(stages: dict, score_breakdown: dict) -> str:
    """Structured Markdown fallback when the LLM call fails or is unavailable."""
    score   = score_breakdown.get("score", 0)
    verdict = score_breakdown.get("verdict", "UNKNOWN")
    notes   = score_breakdown.get("notes", [])

    lines = [
        "## Verification Report",
        "",
        f"**Score:** {score}/100  |  **Verdict:** {verdict}",
        "",
        "### Score Breakdown",
    ]
    if notes:
        lines.extend(f"- {n}" for n in notes)
    else:
        lines.append("- No deductions")

    lines += ["", "### Stage Summaries"]
    for stage, result in stages.items():
        lines.append(f"- **{stage.upper()}**: {result.get('summary', 'No summary')}")

    return "\n".join(lines)


async def generate_report(
    stages:          dict,
    score_breakdown: dict,
    model:           str = "claude",
) -> str:
    """Generate a Markdown verification report via the shared LLM client.

    Args:
        stages:          Results dict from each verification stage.
        score_breakdown: Output of ``scorer.compute_score().to_dict()``.
        model:           LLM model key (see vibe4fpga-llm-client registry).

    Returns:
        Markdown-formatted report string. Falls back to a deterministic
        template when the LLM call raises.
    """
    score   = score_breakdown.get("score", 0)
    verdict = score_breakdown.get("verdict", "UNKNOWN")
    notes   = score_breakdown.get("notes", [])
    breakdown_text = "\n".join(f"  - {n}" for n in notes) if notes else "  - No deductions"

    try:
        return await call_llm(
            messages=[{
                "role":    "user",
                "content": REPORTER_PROMPT.format(
                    results_json=json.dumps(stages, indent=2, default=str)[:5000],
                    score=score,
                    verdict=verdict,
                    breakdown_text=breakdown_text,
                ),
            }],
            system=REPORTER_SYSTEM,
            model=model,
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception:
        return _fallback_report(stages, score_breakdown)
