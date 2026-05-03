"""Tier 2 — pure-logic tests for the simulator-log parser.

Exercises every verdict branch of :func:`parse_sim_log` with deterministic
inputs. No subprocess, no file I/O — safe on every CI runner.
"""

from __future__ import annotations

from eda_bridge_mcp.sim_log import parse_sim_log


def test_clean_pass_log() -> None:
    """Classic self-checking TB with PASS banner."""
    out = """
    VCD info: dumpfile tb.vcd opened for output.
    Test 1: reset behavior ... PASS
    Test 2: overflow        ... PASS
    All tests passed
    $finish called at 1000 (1ns)
    """
    s = parse_sim_log(out)
    assert s.verdict == "pass"
    assert s.pass_count >= 2
    assert s.fail_count == 0
    assert s.error_count == 0
    assert s.fatal_count == 0


def test_mixed_pass_fail_log() -> None:
    """Explicit FAIL dominates — verdict must be fail even with passes present."""
    out = """
    Test 1: reset ... PASS
    Test 2: overflow ... FAIL (expected 0xFF, got 0x00 at time 135ns)
    $finish called at 500 (1ns)
    """
    s = parse_sim_log(out)
    assert s.verdict == "fail"
    assert s.pass_count == 1
    assert s.fail_count == 1


def test_fatal_overrides_pass() -> None:
    """A $fatal aborts the sim; even if PASS was printed earlier, verdict is error."""
    out = """
    Test 1: reset ... PASS
    $fatal called from tb.sv line 42 — timeout waiting for ready
    """
    s = parse_sim_log(out)
    assert s.verdict == "error"
    assert s.fatal_count == 1


def test_assertion_failure() -> None:
    out = "ASSERTION FAILED at 120ns: data_valid should be stable for 3 cycles"
    s = parse_sim_log(out)
    assert s.verdict == "fail"
    assert s.assertions_failed == 1
    assert len(s.error_lines) == 1


def test_dollar_error_marker() -> None:
    out = "[   135] $error(\"Counter did not saturate\")"
    s = parse_sim_log(out)
    assert s.verdict == "fail"
    assert s.error_count == 1


def test_empty_log_is_unclear() -> None:
    """A testbench that just `$finish`es without reporting should NOT be
    silently treated as passing — it's unclear."""
    s = parse_sim_log("")
    assert s.verdict == "unclear"
    assert s.pass_count == s.fail_count == s.error_count == s.fatal_count == 0


def test_stderr_is_also_scanned() -> None:
    """Verilator writes some assertion output to stderr — parser must see it."""
    s = parse_sim_log(stdout="", stderr="ASSERTION FAILED at 200ns: foo")
    assert s.verdict == "fail"
    assert s.assertions_failed == 1


def test_uvm_fatal_is_treated_as_error() -> None:
    out = "UVM_FATAL @ 500ns tb.env[test]: Constraint failed"
    s = parse_sim_log(out)
    assert s.verdict == "error"
    assert s.fatal_count == 1


def test_warnings_do_not_change_verdict() -> None:
    out = """
    $warning: recoverable anomaly at 50ns
    Test 1 PASSED
    """
    s = parse_sim_log(out)
    assert s.verdict == "pass"
    assert s.warning_count == 1


def test_error_lines_are_capped() -> None:
    many_errors = "\n".join(f"ERROR: assertion {i} failed" for i in range(50))
    s = parse_sim_log(many_errors, max_error_lines=5)
    assert len(s.error_lines) == 5


def test_to_dict_roundtrip() -> None:
    s = parse_sim_log("Test PASSED\n")
    d = s.to_dict()
    assert d["verdict"] == "pass"
    assert d["pass_count"] == 1
    # All 9 fields present.
    expected_keys = {
        "verdict", "pass_count", "fail_count", "error_count",
        "fatal_count", "warning_count", "assertions_passed",
        "assertions_failed", "error_lines",
    }
    assert set(d.keys()) == expected_keys
