"""CodeReview Skill — FPGA design pitfall checker.

Combines:
  1. Fast static lint (verilator + verible, ~12ms) — external, deferred to
     eda-bridge-mcp; this skill focuses on the LLM-driven deep analysis.
  2. LLM deep analysis with FPGA-specific checklist.

Checklist:
  - Latch inference (incomplete if/case without default)
  - Combinational loops
  - Multiple drivers on same signal
  - Clock domain crossing without synchronizer
  - Sensitivity list completeness
  - Signed/unsigned mixing
  - Unconnected/dangling ports
  - Reset coverage (all flip-flops reset?)
  - Non-blocking/blocking assignment mixing
"""

from __future__ import annotations

from pathlib import Path

from .._llm import call_llm, parse_json_response

CODE_REVIEW_SYSTEM = """\
You are an expert FPGA RTL reviewer. Analyze the provided Verilog/SystemVerilog code
for design issues. Focus on FPGA-specific pitfalls.

Output ONLY valid JSON array — each item is a finding:
[
  {
    "severity": "error|warning|info",
    "category": "latch|cdc|multi_driver|reset|sensitivity|signedness|unconnected|style",
    "line":     integer_or_null,
    "signal":   "signal_name_or_null",
    "message":  "concise description of the issue",
    "fix":      "suggested fix"
  }
]

Return [] if no issues found.

FPGA pitfalls to check:
1. Latch inference — if/case without else/default in combinational always block
2. Combinational loops — output feeds back to input without register
3. Multiple drivers — same signal assigned in multiple always blocks
4. CDC issues — signals crossing clock domains without 2-FF synchronizer
5. Sensitivity list — @(*) or @(posedge clk) completeness
6. Signed/unsigned — mixing signed and unsigned in arithmetic without explicit cast
7. Non-blocking in combinational — using <= in always @(*) blocks
8. Blocking in sequential — using = in always @(posedge clk) blocks (except for temp vars)
9. Reset coverage — any FF without reset
10. initial blocks — should not exist in synthesizable RTL
"""


async def review_code(
    rtl_code:  str,
    file_name: str = "unknown.v",
    model:     str = "claude",
) -> dict:
    """Run LLM-based code review on RTL code.

    Returns:
        {
            "findings": [{severity, category, line, signal, message, fix}],
            "error_count":   int,
            "warning_count": int,
            "summary":       str,
        }
    """
    raw = await call_llm(
        messages=[{
            "role":    "user",
            "content": f"Review this RTL file ({file_name}):\n\n```verilog\n{rtl_code}\n```",
        }],
        system=CODE_REVIEW_SYSTEM,
        model=model,
        temperature=0.1,
    )

    findings = parse_json_response(raw)
    if not isinstance(findings, list):
        findings = []

    errors   = sum(1 for f in findings if f.get("severity") == "error")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")

    return {
        "findings":      findings,
        "error_count":   errors,
        "warning_count": warnings,
        "summary": (
            f"{errors} error(s), {warnings} warning(s) found in {file_name}"
            if (errors + warnings) > 0
            else f"No issues found in {file_name}"
        ),
    }


async def review_file(file_path: str, model: str = "claude") -> dict:
    """Review an RTL file from disk."""
    fp = Path(file_path)
    if not fp.exists():
        return {"error": f"File not found: {file_path}"}
    code = fp.read_text(encoding="utf-8", errors="replace")
    return await review_code(code, file_name=fp.name, model=model)
