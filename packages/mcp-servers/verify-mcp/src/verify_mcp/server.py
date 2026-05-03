"""verify-mcp — FastMCP server entrypoint.

Exposes two LLM-backed tools absorbed from the retired ``packages/skills/``
monolith:

* ``generate_testbench`` — synthesise a self-checking SystemVerilog testbench
  (``verify_mcp.skills.testbench_gen``).
* ``score_verification`` — ingest lint/sim/formal/synth reports and produce a
  composite score + narrative report
  (``verify_mcp.skills.verification``).

LLM calls flow through the shared :mod:`vibe4fpga_llm_client` adapter library,
so the backend is selected at runtime via ``VIBE4FPGA_LLM`` / API-key env vars.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .skills.testbench_gen.skill import run as testbench_gen_run
from .skills.verification.pipeline import run as verification_run

mcp = FastMCP("verify-mcp")


# ── Tool input schemas ────────────────────────────────────────────────────────

class GenerateTestbenchInput(BaseModel):
    """Inputs for ``generate_testbench``."""

    rtl_code:  str              = Field(..., description="Verilog/SystemVerilog module under test.")
    spec:      str | None       = Field(None,  description="Natural-language description of intended behaviour.")
    coverage:  list[str] | None = Field(None,  description="Named coverage goals (e.g. 'reset_recovery', 'backpressure').")
    simulator: str              = Field("iverilog", description="Target simulator: 'iverilog' | 'verilator' | 'xsim'.")
    model:    str | None        = Field(None,  description="LLM model key (see vibe4fpga-llm-client registry).")


class ScoreVerificationInput(BaseModel):
    """Inputs for ``score_verification``."""

    rtl_code:      str               = Field(..., description="RTL source being verified.")
    spec:          str | None        = Field(None,  description="Optional spec text for spec-consistency stage.")
    sim_log:       str | None        = Field(None,  description="Raw simulator log for pass/fail parsing.")
    lint_report:   str | None        = Field(None,  description="Lint-stage report (Verilator/Icarus/SpyGlass).")
    formal_report: str | None        = Field(None,  description="Formal (SymbiYosys) report.")
    synth_report:  str | None        = Field(None,  description="Synthesis + timing report summary.")
    model:         str | None        = Field(None,  description="LLM model key for narrative report.")


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def generate_testbench(inputs: GenerateTestbenchInput) -> dict:
    """Generate a SystemVerilog testbench for the supplied RTL module.

    Produces a self-checking testbench with reset handling, boundary-condition
    stimulus, SVA assertions (both simulation and ``ifdef FORMAL`` paths),
    a covergroup tracking coverage points, and a watchdog timeout. Coverage
    goals passed through ``inputs.coverage`` are woven into the prompt.

    Returns:
        ``{testbench_code, coverage_points, assertion_count, simulator,
           warnings, summary}``.
    """
    return await testbench_gen_run(
        rtl_code  = inputs.rtl_code,
        spec      = inputs.spec,
        coverage  = inputs.coverage,
        simulator = inputs.simulator,
        model     = inputs.model,
    )


@mcp.tool()
async def score_verification(inputs: ScoreVerificationInput) -> dict:
    """Multi-stage verification scoring across lint, sim, formal, synth, and spec.

    Each optional report is parsed deterministically (error/warning counts,
    simulator pass/fail markers, formal failures, WNS extraction). When a
    ``spec`` is provided, a further LLM self-check extracts spec clauses and
    verifies them against ``rtl_code``. The stage outputs feed
    :func:`skills.verification.scorer.compute_score`, and an LLM narrative
    renders the Markdown report.

    Returns a verdict per stage plus an ``overall_verdict`` (PASS/REVIEW/FAIL)
    and a Markdown narrative under ``report_md``.
    """
    return await verification_run(
        rtl_code      = inputs.rtl_code,
        spec          = inputs.spec,
        sim_log       = inputs.sim_log,
        lint_report   = inputs.lint_report,
        formal_report = inputs.formal_report,
        synth_report  = inputs.synth_report,
        model         = inputs.model,
    )


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Console-script entry point for ``verify-mcp``."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
