"""Tier 2 — deterministic scoring unit tests.

``scorer.compute_score`` is the pure-logic backbone of ``score_verification``.
These tests lock in the deduction tables documented in ``scorer.py`` so the
verdict boundaries (PASS >= 85, REVIEW 60-84, FAIL < 60) are never silently
rewritten.
"""

from __future__ import annotations

from verify_mcp.skills.verification.scorer import compute_score


def test_clean_run_is_pass() -> None:
    bd = compute_score(
        lint_results={"error_count": 0, "warning_count": 0},
        sim_results={"failed_scenarios": 0},
        formal_results={"property_failures": 0},
        synthesis_results={"timing": {"wns": 1.23}, "timing_violations": 0},
        spec_check_results=[],
    )
    assert bd.total == 100.0
    assert bd.verdict == "PASS"


def test_lint_deducts_errors_and_warnings() -> None:
    bd = compute_score(
        lint_results={"error_count": 2, "warning_count": 3},
    )
    # 2 errors * 10 + 3 warnings * 2 = 26
    assert bd.lint_deductions == 26.0
    assert bd.total == 74.0
    assert bd.verdict == "REVIEW"


def test_timing_violation_triggers_20pt_hit() -> None:
    bd = compute_score(
        synthesis_results={"timing": {"wns": -0.5}, "timing_violations": 1},
    )
    assert bd.timing_deductions == 20.0
    assert bd.total == 80.0
    assert bd.verdict == "REVIEW"


def test_fail_verdict_crosses_60_boundary() -> None:
    # 3 sim failures = -45 pts; add 1 failed spec clause = -12 = 57 ⇒ FAIL.
    bd = compute_score(
        sim_results={"failed_scenarios": 3},
        spec_check_results=[{"status": "FAIL", "clause": "x"}],
    )
    assert bd.sim_deductions == 45.0
    assert bd.spec_deductions == 12.0
    assert bd.total == 43.0
    assert bd.verdict == "FAIL"


def test_to_dict_roundtrip_fields() -> None:
    bd = compute_score(lint_results={"error_count": 1, "warning_count": 0})
    d = bd.to_dict()
    assert d["score"] == 90.0
    assert d["verdict"] == "PASS"
    assert d["breakdown"]["lint"] == 10.0
    assert any("Lint: 1 error" in n for n in d["notes"])


def test_declared_clauses_do_not_deduct_but_surface_note() -> None:
    bd = compute_score(
        spec_check_results=[
            {"status": "DECLARED", "clause": "auto-choice"},
        ],
    )
    assert bd.spec_deductions == 0.0
    assert bd.total == 100.0
    assert any("DECLARED" in n for n in bd.notes)
