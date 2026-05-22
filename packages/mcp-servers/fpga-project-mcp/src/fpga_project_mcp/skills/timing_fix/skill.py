"""TimingFix Skill — analyze Vivado timing report and suggest fixes.

Three optimization strategies (from design doc):
  1. Register pipeline    — insert FF, +1 cycle latency (recommended first)
  2. Multi-cycle path     — set_multicycle_path -setup 2 (when rushing)
  3. Eliminate comparison — one-hot / MSB flag (long-term optimal)
"""

from __future__ import annotations

import json
import re

from .._llm import call_llm, parse_json_response

TIMING_FIX_SYSTEM = """\
You are a senior FPGA timing closure engineer. Analyze the timing violation report
and provide specific, actionable fixes.

For each critical path violation, recommend one or more of these strategies:
  1. Register pipeline:   Insert a pipeline register (FF) to break the critical path.
                          Adds 1 cycle latency. Best for most cases.
  2. Multi-cycle path:    Add set_multicycle_path -setup 2 constraint.
                          Hides the violation — use only when truly needed,
                          ALWAYS add a comment explaining why it's safe.
  3. Logic restructuring: Eliminate comparison logic (use one-hot encoding,
                          MSB flag, or pre-computed signals).
                          Best long-term but requires RTL changes and re-simulation.

Output JSON array:
[
  {
    "path_id":        "string — e.g. 'path_1'",
    "start_point":    {"file": str, "line": int, "signal": str},
    "end_point":      {"file": str, "line": int, "signal": str},
    "slack_ns":       float,
    "strategies": [
      {
        "strategy":    "pipeline|multicycle|restructure",
        "description": "specific change to make",
        "rtl_snippet": "code example if applicable",
        "xdc_snippet": "XDC constraint if applicable",
        "tradeoff":    "latency / safety note"
      }
    ],
    "recommended":    "pipeline|multicycle|restructure"
  }
]
"""

TIMING_REPORT_PROMPT = """\
Timing Report:
```
{timing_report}
```

WNS: {wns} ns  |  TNS: {tns} ns

RTL Context (critical path signals):
```verilog
{rtl_context}
```

Suggest fixes for all paths with negative slack.
"""


async def run(
    timing_report: str,
    wns:           float | None = None,
    tns:           float | None = None,
    rtl_context:   str = "",
    model:         str = "claude",
) -> dict:
    """Run TimingFix analysis.

    Args:
        timing_report: Raw Vivado timing summary report text.
        wns:           Worst Negative Slack (ns). Auto-extracted if None.
        tns:           Total Negative Slack (ns). Auto-extracted if None.
        rtl_context:   Relevant RTL code snippets for context.
        model:         LLM model key (see vibe4fpga-llm-client registry).

    Returns:
        {
            "violations_found": int,
            "wns":  float,
            "tns":  float,
            "fixes": [...],
            "summary": str,
        }
    """
    # Auto-extract WNS/TNS from report text when caller didn't pass them.
    if wns is None:
        m = re.search(r"WNS\(ns\)\s*([-\d.]+)", timing_report)
        wns = float(m.group(1)) if m else 0.0
    if tns is None:
        m = re.search(r"TNS\(ns\)\s*([-\d.]+)", timing_report)
        tns = float(m.group(1)) if m else 0.0

    if wns >= 0:
        return {
            "violations_found": 0,
            "wns":   wns,
            "tns":   tns,
            "fixes": [],
            "summary": f"Timing PASSED: WNS={wns:.3f} ns, TNS={tns:.3f} ns. No fixes needed.",
        }

    # Match both summary form "Slack (VIOLATED): -2.345ns" and bare "Slack: -X".
    violations_found = len(
        re.findall(r"Slack\s*(?:\([^)]*\))?\s*:\s*-[\d.]+", timing_report)
    )
    # Fall back to 1 when the report only carries an aggregate WNS without
    # per-path slack lines — wns<0 already proved there is at least one.
    if violations_found == 0:
        violations_found = 1

    raw = await call_llm(
        messages=[{
            "role":    "user",
            "content": TIMING_REPORT_PROMPT.format(
                timing_report=timing_report[:4000],  # cap to avoid huge context
                wns=wns,
                tns=tns,
                rtl_context=rtl_context[:2000],
            ),
        }],
        system=TIMING_FIX_SYSTEM,
        model=model,
        temperature=0.1,
    )

    try:
        fixes = parse_json_response(raw)
    except json.JSONDecodeError:
        fixes = [{"raw_response": raw}]

    return {
        "violations_found": violations_found,
        "wns":   wns,
        "tns":   tns,
        "fixes": fixes,
        "summary": (
            f"TimingFix: WNS={wns:.3f} ns, TNS={tns:.3f} ns — "
            f"{len(fixes)} critical path(s) analyzed"
        ),
    }
