"""Verification scoring algorithm.

Score starts at 100. Deductions:
  Lint error    -10/item
  Lint warning   -2/item
  Sim failure   -15/scenario
  Coverage  1%   -0.5 below target
  Timing violation -20/path
  Spec clause not met -12/clause

Verdict:
  ≥ 85: PASS  (can submit)
  60-84: REVIEW  (needs attention)
  < 60:  FAIL  (blocked)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoreBreakdown:
    initial: float = 100.0
    lint_deductions: float = 0.0
    sim_deductions: float = 0.0
    coverage_deductions: float = 0.0
    timing_deductions: float = 0.0
    spec_deductions: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return max(0.0, self.initial - (
            self.lint_deductions
            + self.sim_deductions
            + self.coverage_deductions
            + self.timing_deductions
            + self.spec_deductions
        ))

    @property
    def verdict(self) -> str:
        t = self.total
        if t >= 85:
            return "PASS"
        if t >= 60:
            return "REVIEW"
        return "FAIL"

    def to_dict(self) -> dict:
        return {
            "score":   round(self.total, 1),
            "verdict": self.verdict,
            "breakdown": {
                "lint":     round(self.lint_deductions, 1),
                "sim":      round(self.sim_deductions, 1),
                "coverage": round(self.coverage_deductions, 1),
                "timing":   round(self.timing_deductions, 1),
                "spec":     round(self.spec_deductions, 1),
            },
            "notes": self.notes,
        }


def compute_score(
    lint_results: dict | None = None,
    sim_results: dict | None = None,
    formal_results: dict | None = None,
    synthesis_results: dict | None = None,
    spec_check_results: list | None = None,
    coverage_pct: float | None = None,
    coverage_target: float = 80.0,
) -> ScoreBreakdown:
    """Compute the overall verification score from 5 layer results."""
    bd = ScoreBreakdown()

    # ── Layer 1: Lint ─────────────────────────────────────────────────────────
    if lint_results:
        errors   = lint_results.get("error_count", 0)
        warnings = lint_results.get("warning_count", 0)
        bd.lint_deductions = errors * 10.0 + warnings * 2.0
        if errors:
            bd.notes.append(f"Lint: {errors} error(s) → -{errors * 10} pts")
        if warnings:
            bd.notes.append(f"Lint: {warnings} warning(s) → -{warnings * 2} pts")

    # ── Layer 2: Simulation ───────────────────────────────────────────────────
    if sim_results:
        failed_scenarios = sim_results.get("failed_scenarios", 0)
        bd.sim_deductions = failed_scenarios * 15.0
        if failed_scenarios:
            bd.notes.append(f"Simulation: {failed_scenarios} scenario(s) failed → -{failed_scenarios * 15} pts")

        # Coverage
        if coverage_pct is not None:
            shortfall = max(0.0, coverage_target - coverage_pct)
            bd.coverage_deductions = shortfall * 0.5
            if shortfall > 0:
                bd.notes.append(
                    f"Coverage: {coverage_pct:.1f}% < target {coverage_target:.0f}% "
                    f"→ -{bd.coverage_deductions:.1f} pts"
                )

    # ── Layer 3: Formal Verification ─────────────────────────────────────────
    if formal_results:
        formal_failures = formal_results.get("property_failures", 0)
        bd.spec_deductions += formal_failures * 12.0
        if formal_failures:
            bd.notes.append(
                f"Formal: {formal_failures} property failure(s) → -{formal_failures * 12} pts"
            )

    # ── Layer 4: Synthesis/Timing ─────────────────────────────────────────────
    if synthesis_results:
        timing = synthesis_results.get("timing", {})
        wns = timing.get("wns")
        if wns is not None and wns < 0:
            # Count paths with negative slack (approximation)
            violations = max(1, synthesis_results.get("timing_violations", 1))
            bd.timing_deductions = violations * 20.0
            bd.notes.append(
                f"Timing: WNS={wns:.3f} ns, {violations} violation(s) → -{bd.timing_deductions:.0f} pts"
            )

    # ── Layer 5: Spec Compliance ──────────────────────────────────────────────
    if spec_check_results:
        failed_clauses = sum(1 for r in spec_check_results if r.get("status") == "FAIL")
        bd.spec_deductions += failed_clauses * 12.0
        declared_clauses = sum(1 for r in spec_check_results if r.get("status") == "DECLARED")
        if failed_clauses:
            bd.notes.append(
                f"Spec: {failed_clauses} clause(s) not met → -{failed_clauses * 12} pts"
            )
        if declared_clauses:
            bd.notes.append(
                f"Spec: {declared_clauses} clause(s) DECLARED (autonomous decisions — review required)"
            )

    return bd
