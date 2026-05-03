"""Five-stage RTL verification scoring pipeline.

Stage | Input                         | Role
------+-------------------------------+-----------------------------------------
  1   | lint_report  (str, optional)  | Count errors / warnings
  2   | sim_log      (str, optional)  | Infer pass/fail scenario count
  3   | formal_report(str, optional)  | Count SymbiYosys property failures
  4   | synth_report (str, optional)  | Extract WNS + violation count
  5   | spec         (str, optional)  | LLM spec-compliance self-check

Unlike the old FastAPI-router version, this pipeline no longer shells out to
``eda-bridge-mcp``. Each stage ingests raw report text produced upstream by a
host or by another MCP, parses it deterministically, and feeds the numbers
into :func:`scorer.compute_score`. :func:`reporter.generate_report` then turns
the combined results into a Markdown narrative via the shared LLM client.
"""

from __future__ import annotations

import re

from .reporter import generate_report
from .scorer import compute_score

SPEC_CHECK_SYSTEM = """\
You are an FPGA verification engineer cross-checking RTL against its spec.

For each concrete requirement you can extract from the spec, emit one JSON
object with:
  - "clause":  the spec fragment being checked
  - "status":  "PASS" | "FAIL" | "DECLARED"
  - "evidence": brief reference to the RTL (signal name, line range, or short
                 quoted snippet) justifying the verdict

"DECLARED" means the RTL makes an autonomous implementation choice that the
spec neither mandates nor forbids; flag these so a human can confirm.

Output ONLY a JSON array. If the spec is empty or unparseable, return [].
"""

SPEC_CHECK_PROMPT = """\
Specification:
{spec}

RTL Code:
```systemverilog
{rtl_code}
```

Return the JSON array of checks.
"""


# ── Stage 1 — Lint ───────────────────────────────────────────────────────────

_LINT_ERROR_PATTERNS = [
    re.compile(r"^\s*%?Error[:\- ]",     re.IGNORECASE | re.MULTILINE),
    re.compile(r"\berror\s*:",           re.IGNORECASE),
]
_LINT_WARNING_PATTERNS = [
    re.compile(r"^\s*%?Warning[:\- ]",   re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bwarning\s*:",         re.IGNORECASE),
]


def parse_lint_report(lint_report: str | None) -> dict:
    """Deterministic lint-report summariser.

    Accepts output from Verilator / Icarus / Verible / SpyGlass. Counts
    unique lines that match ``error``/``warning`` markers. Returns a dict with
    ``error_count``, ``warning_count``, and a ``summary``.
    """
    if not lint_report:
        return {"error_count": 0, "warning_count": 0, "summary": "Lint: no report supplied"}

    errors   = sum(len(p.findall(lint_report)) for p in _LINT_ERROR_PATTERNS)
    warnings = sum(len(p.findall(lint_report)) for p in _LINT_WARNING_PATTERNS)

    return {
        "error_count":   errors,
        "warning_count": warnings,
        "summary": (
            f"Lint: {errors} error(s), {warnings} warning(s)"
        ),
    }


# ── Stage 2 — Simulation ─────────────────────────────────────────────────────

_SIM_FAIL_PATTERNS = [
    re.compile(r"\bFAIL\b",            re.IGNORECASE),
    re.compile(r"\bASSERTION\s+FAILED\b", re.IGNORECASE),
    re.compile(r"\$error",             re.IGNORECASE),
    re.compile(r"\$fatal",             re.IGNORECASE),
    re.compile(r"Test\s+FAILED",       re.IGNORECASE),
]
_SIM_PASS_PATTERNS = [
    re.compile(r"\bPASS\b",            re.IGNORECASE),
    re.compile(r"All tests passed",    re.IGNORECASE),
    re.compile(r"Test\s+PASSED",       re.IGNORECASE),
]


def parse_sim_log(sim_log: str | None) -> dict:
    """Detect failed scenarios in a raw simulator log.

    Heuristic: count occurrences of FAIL / $error / $fatal / "Test FAILED" and
    compare against PASS markers. If neither side fires, we return 0 failures
    and mark the stage ``inconclusive``.
    """
    if not sim_log:
        return {
            "success":          False,
            "failed_scenarios": 0,
            "summary":          "Simulation: no log supplied",
        }

    failures = sum(len(p.findall(sim_log)) for p in _SIM_FAIL_PATTERNS)
    passes   = sum(len(p.findall(sim_log)) for p in _SIM_PASS_PATTERNS)

    if failures == 0 and passes == 0:
        return {
            "success":          False,
            "failed_scenarios": 0,
            "summary":          "Simulation: inconclusive (no PASS/FAIL markers)",
        }

    success = failures == 0 and passes > 0
    return {
        "success":          success,
        "failed_scenarios": failures,
        "summary": (
            f"Simulation: {'PASSED' if success else 'FAILED'} "
            f"({failures} failure marker(s), {passes} pass marker(s))"
        ),
    }


# ── Stage 3 — Formal ─────────────────────────────────────────────────────────

_FORMAL_FAIL_PATTERNS = [
    re.compile(r"FAIL(?:URE)?",               re.IGNORECASE),
    re.compile(r"property\s+.*\s+failed",     re.IGNORECASE),
    re.compile(r"assertion\s+.*\s+failed",    re.IGNORECASE),
]


def parse_formal_report(formal_report: str | None) -> dict:
    """Count failing properties in a SymbiYosys (sby) report."""
    if not formal_report:
        return {
            "property_failures": 0,
            "summary":           "Formal: no report supplied",
        }

    failures = sum(len(p.findall(formal_report)) for p in _FORMAL_FAIL_PATTERNS)
    return {
        "property_failures": failures,
        "summary": (
            f"Formal: {failures} property failure(s)"
            if failures else "Formal: all properties held"
        ),
    }


# ── Stage 4 — Synthesis / Timing ─────────────────────────────────────────────

_WNS_PATTERN        = re.compile(r"WNS\s*\(?ns\)?\s*[:=]?\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_VIOLATION_PATTERN  = re.compile(r"Slack\s*\(?VIOLATED\)?\s*:\s*(-\d+(?:\.\d+)?)",  re.IGNORECASE)


def parse_synth_report(synth_report: str | None) -> dict:
    """Extract WNS + violation count from a synthesis/timing summary."""
    if not synth_report:
        return {
            "timing":             {"wns": None},
            "timing_violations":  0,
            "summary":            "Synthesis: no report supplied",
        }

    wns_match  = _WNS_PATTERN.search(synth_report)
    wns        = float(wns_match.group(1)) if wns_match else None
    violations = len(_VIOLATION_PATTERN.findall(synth_report))
    if wns is not None and wns < 0 and violations == 0:
        violations = 1

    return {
        "timing":            {"wns": wns},
        "timing_violations": violations,
        "summary": (
            f"Synthesis: WNS={wns if wns is not None else 'n/a'} ns, "
            f"{violations} violation(s)"
        ),
    }


# ── Stage 5 — Spec compliance (LLM) ──────────────────────────────────────────

async def spec_compliance_check(
    spec:     str,
    rtl_code: str,
    model:    str = "claude",
) -> dict:
    """LLM self-check: does the RTL satisfy each extracted spec clause?"""
    if not spec:
        return {
            "checks":           [],
            "failed_clauses":   0,
            "declared_clauses": 0,
            "summary":          "Spec compliance: skipped (no spec supplied)",
        }

    # Local import keeps the stage dependency explicit without pulling the
    # llm-client adapter chain into this module at import-time.
    from .._llm import call_llm, parse_json_response

    raw = await call_llm(
        messages=[{
            "role":    "user",
            "content": SPEC_CHECK_PROMPT.format(
                spec=spec[:4000],
                rtl_code=rtl_code[:6000],
            ),
        }],
        system=SPEC_CHECK_SYSTEM,
        model=model,
        temperature=0.1,
    )

    try:
        checks = parse_json_response(raw)
        if not isinstance(checks, list):
            checks = []
    except Exception:
        checks = []

    failed   = sum(1 for c in checks if c.get("status") == "FAIL")
    declared = sum(1 for c in checks if c.get("status") == "DECLARED")

    return {
        "checks":           checks,
        "failed_clauses":   failed,
        "declared_clauses": declared,
        "summary": (
            f"Spec compliance: {failed} failed, {declared} declared "
            f"(of {len(checks)} clause(s) extracted)"
        ),
    }


# ── Pipeline entry point ─────────────────────────────────────────────────────

async def run(
    rtl_code:      str,
    spec:          str | None = None,
    sim_log:       str | None = None,
    lint_report:   str | None = None,
    formal_report: str | None = None,
    synth_report:  str | None = None,
    model:         str | None = None,
) -> dict:
    """Score a verification run from pre-collected stage reports.

    Args:
        rtl_code:      RTL source being verified (fed to stage 5 LLM check).
        spec:          Optional spec text. When present, stage 5 runs.
        sim_log:       Raw simulator log for pass/fail inference.
        lint_report:   Lint-stage report (Verilator/Icarus/SpyGlass).
        formal_report: Formal-stage (SymbiYosys) report.
        synth_report:  Synthesis + timing summary.
        model:         LLM model key for the narrative + spec-compliance call.

    Returns:
        {
            "stages":  {lint, sim, formal, synth, spec?},
            "score":   float,
            "verdict": "PASS" | "REVIEW" | "FAIL",
            "breakdown": {...},
            "notes":   [...],
            "report_md": str,
        }
    """
    effective_model = model or "claude"

    stages = {
        "lint":   parse_lint_report(lint_report),
        "sim":    parse_sim_log(sim_log),
        "formal": parse_formal_report(formal_report),
        "synth":  parse_synth_report(synth_report),
    }

    if spec:
        stages["spec"] = await spec_compliance_check(spec, rtl_code, effective_model)

    score_bd = compute_score(
        lint_results       = stages["lint"],
        sim_results        = stages["sim"],
        formal_results     = stages["formal"],
        synthesis_results  = stages["synth"],
        spec_check_results = stages.get("spec", {}).get("checks"),
    )
    score_dict = score_bd.to_dict()

    report_md = await generate_report(stages, score_dict, model=effective_model)

    return {
        "stages":          stages,
        "score":           score_dict["score"],
        "verdict":         score_dict["verdict"],
        "overall_verdict": score_dict["verdict"],
        "breakdown":       score_dict["breakdown"],
        "notes":           score_dict["notes"],
        "report_md":       report_md,
    }
