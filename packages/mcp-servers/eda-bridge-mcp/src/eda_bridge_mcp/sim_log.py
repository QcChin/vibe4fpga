"""Heuristic parser for simulator stdout (Icarus ``vvp``, Verilator, xsim).

Fills the gap between ``run_simulation`` returning raw stdout and a caller
actually knowing whether the testbench passed. The parser is deterministic
and pattern-based — no LLM, no external tools — so it's safe to run inline
inside the async tool body.

Marker conventions recognised (case-insensitive where sensible):

* SystemVerilog system tasks
  * ``$fatal``           — always a hard failure
  * ``$error``           — counted as an error (not a fatal), non-zero contributes to a ``fail`` verdict
  * ``$warning``         — counted but does not change verdict
* Free-form string conventions common in hand-rolled self-checking TBs
  * ``Test PASSED`` / ``PASS`` / ``All tests passed`` / starred banner lines → +1 pass
  * ``Test FAILED`` / ``FAIL``                                               → +1 fail
* Immediate assertion output from iverilog / Verilator
  * ``ASSERTION FAILED`` / ``assert failed``                                 → +1 assertion_failed
  * ``ASSERTION PASSED`` / ``assert passed``                                 → +1 assertion_passed
* UVM-style messages (best effort — UVM on iverilog is limited anyway)
  * ``UVM_FATAL`` / ``UVM_ERROR``                                            → fatal / error counters

Verdict is computed from counts, not single tokens, so a TB that prints
``"this is the pass/fail decider"`` doesn't spuriously mark the run
passing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


# ── Pattern library ──────────────────────────────────────────────────────────
# Each pattern matches one counter's worth of evidence when it matches a line.

_RX_PASS = re.compile(
    r"(?:^|\s|\*|\])"
    r"(?:test\s+(?:\d+\s+)?passed"
    r"|all\s+tests?\s+passed"
    r"|\*{2,}\s*pass\b"
    r"|PASS\b"                 # matches "PASS", "PASS:", "PASS —"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_RX_FAIL = re.compile(
    r"(?:^|\s|\*|\])"
    r"(?:test\s+(?:\d+\s+)?failed"
    r"|\*{2,}\s*fail\b"
    r"|FAIL\b"                 # matches "FAIL", "FAIL:", "FAIL —"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_RX_FATAL           = re.compile(r"\$fatal\b|\bUVM_FATAL\b",        re.IGNORECASE)
_RX_ERROR           = re.compile(r"\$error\b|\bUVM_ERROR\b|^ERROR:", re.IGNORECASE | re.MULTILINE)
_RX_WARNING         = re.compile(r"\$warning\b|\bUVM_WARNING\b",    re.IGNORECASE)
_RX_ASSERT_FAILED   = re.compile(r"assertion\s+failed|\bassert\s+failed",    re.IGNORECASE)
_RX_ASSERT_PASSED   = re.compile(r"assertion\s+passed|\bassert\s+passed",    re.IGNORECASE)
_RX_ERROR_LINE_HINT = re.compile(
    r"^(?:ERROR:|.*\$error|.*\$fatal|.*UVM_(?:ERROR|FATAL)|.*ASSERTION FAILED.*)",
    re.IGNORECASE | re.MULTILINE,
)


# ── Public data model ────────────────────────────────────────────────────────

@dataclass
class SimLogSummary:
    """Summary of what a simulator stdout actually said.

    ``verdict`` collapses the counters into a single word for downstream
    scoring. Value legend:

    * ``pass``    — at least one PASS marker, zero fails / errors / fatals
    * ``fail``    — any explicit FAIL marker or ``$error`` / assertion failure
    * ``error``   — any ``$fatal`` / ``UVM_FATAL`` (regardless of PASS markers;
                    a fatal overrides a pass banner because the sim aborted)
    * ``unclear`` — no markers at all; caller must decide (often happens when
                    a testbench terminates on ``$finish`` without reporting)
    """

    verdict:           str
    pass_count:        int
    fail_count:        int
    error_count:       int
    fatal_count:       int
    warning_count:     int
    assertions_passed: int
    assertions_failed: int
    error_lines:       list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Parser ───────────────────────────────────────────────────────────────────

def parse_sim_log(stdout: str, *, stderr: str = "", max_error_lines: int = 10) -> SimLogSummary:
    """Parse simulator stdout (+ optional stderr) into a counters-based summary.

    Both streams are scanned; most simulators mix user ``$display`` output
    with system messages across both, so inspecting only one is fragile.

    Args:
        stdout:          raw simulator stdout.
        stderr:          optional stderr (Verilator writes asserts here).
        max_error_lines: cap on how many offending lines are surfaced in
                         ``error_lines`` so tool responses stay bounded.
    """
    combined = (stdout or "") + "\n" + (stderr or "")

    pass_count        = len(_RX_PASS.findall(combined))
    fail_count        = len(_RX_FAIL.findall(combined))
    fatal_count       = len(_RX_FATAL.findall(combined))
    error_count       = len(_RX_ERROR.findall(combined))
    warning_count     = len(_RX_WARNING.findall(combined))
    assertions_failed = len(_RX_ASSERT_FAILED.findall(combined))
    assertions_passed = len(_RX_ASSERT_PASSED.findall(combined))

    error_lines = [
        line.strip()
        for line in _RX_ERROR_LINE_HINT.findall(combined)
    ][:max_error_lines]

    # Verdict precedence: fatal > fail/error > pass > unclear.
    if fatal_count > 0:
        verdict = "error"
    elif fail_count > 0 or error_count > 0 or assertions_failed > 0:
        verdict = "fail"
    elif pass_count > 0 or assertions_passed > 0:
        verdict = "pass"
    else:
        verdict = "unclear"

    return SimLogSummary(
        verdict=verdict,
        pass_count=pass_count,
        fail_count=fail_count,
        error_count=error_count,
        fatal_count=fatal_count,
        warning_count=warning_count,
        assertions_passed=assertions_passed,
        assertions_failed=assertions_failed,
        error_lines=error_lines,
    )
