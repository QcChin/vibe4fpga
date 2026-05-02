"""verify-mcp — FastMCP server entrypoint.

Phase A status (this file):
    Skeleton only. The skill modules under :mod:`verify_mcp.skills` were
    copied verbatim from the retired ``packages/skills/`` package and still
    call the old FastAPI llm-router over HTTP. Phase B rewires them to
    :func:`vibe4fpga_llm_client.adapter_from_env` and fleshes out the tool
    bodies below.

The tool signatures here are the contract Phase B must preserve — they are
also the schema source for ``skill.yaml`` and the gen-skills generator.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

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

_PHASE_B_SENTINEL = (
    "verify-mcp tools are stubbed in Phase A. Phase B rewires the copied "
    "skill logic to use vibe4fpga_llm_client.adapter_from_env() directly. "
    "Track progress in /Users/naspter/.claude/plans/cheeky-kindling-bachman.md."
)


@mcp.tool()
async def generate_testbench(inputs: GenerateTestbenchInput) -> dict:
    """Generate a SystemVerilog testbench for the supplied RTL module.

    Phase A: stubbed. Phase B wires this to
    :mod:`verify_mcp.skills.testbench_gen` and the shared LLM client.
    """
    raise NotImplementedError(_PHASE_B_SENTINEL)


@mcp.tool()
async def score_verification(inputs: ScoreVerificationInput) -> dict:
    """Multi-stage verification scoring across lint, sim, formal, synth, and spec.

    Returns a verdict per stage plus an overall_verdict (PASS/WARN/FAIL) and
    a Markdown narrative report.

    Phase A: stubbed. Phase B wires this to
    :mod:`verify_mcp.skills.verification` and the shared LLM client.
    """
    raise NotImplementedError(_PHASE_B_SENTINEL)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Console-script entry point for ``verify-mcp``."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
