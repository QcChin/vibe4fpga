"""Verification report generator — LLM narrative synthesis.

The reporter aggregates data from all 5 verification layers and calls
LLM to produce a human-readable Markdown report with actionable recommendations.
"""

from __future__ import annotations

import json

import httpx

REPORTER_SYSTEM = """\
You are an FPGA verification engineer writing a concise technical report.

Given verification results from 5 layers (Lint, Simulation, Formal, Synthesis, Spec),
write a Markdown report that:

1. Opens with overall verdict (PASS/REVIEW/FAIL) and numeric score
2. Has one section per layer with key findings (bullet points)
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


async def generate_report(
    layer_results: dict,
    score_breakdown: dict,
    router_url: str = "http://localhost:8765",
    model: str = "claude",
) -> str:
    """Generate Markdown verification report via LLM.

    Args:
        layer_results:   Results dict from each verification layer.
        score_breakdown: Output of scorer.compute_score().to_dict().
        router_url:      LLM Router URL.

    Returns:
        Markdown-formatted report string.
    """
    score   = score_breakdown.get("score", 0)
    verdict = score_breakdown.get("verdict", "UNKNOWN")
    notes   = score_breakdown.get("notes", [])
    breakdown_text = "\n".join(f"  - {n}" for n in notes) if notes else "  - No deductions"

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{router_url}/chat",
            json={
                "messages": [{
                    "role": "user",
                    "content": REPORTER_PROMPT.format(
                        results_json=json.dumps(layer_results, indent=2, default=str)[:5000],
                        score=score,
                        verdict=verdict,
                        breakdown_text=breakdown_text,
                    ),
                }],
                "system":      REPORTER_SYSTEM,
                "model":       model,
                "temperature": 0.3,
                "stream":      False,
            },
        )

        if resp.status_code == 200:
            return resp.json()["content"]

    # Fallback: structured report without LLM
    lines = [
        f"## Verification Report",
        f"",
        f"**Score:** {score}/100  |  **Verdict:** {verdict}",
        f"",
        f"### Score Breakdown",
    ]
    for note in notes:
        lines.append(f"- {note}")
    lines += ["", "### Layer Results"]
    for layer, result in layer_results.items():
        lines.append(f"- **{layer.upper()}**: {result.get('summary', 'No summary')}")

    return "\n".join(lines)
