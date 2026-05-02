"""TestbenchGen Skill — generate coverage-driven testbench from module spec.

Generated testbench includes:
  - Basic write-read and boundary condition scenarios
  - Reset test (state integrity after reset)
  - SVA assertions for all interface signals (≥1 per signal)
  - Protocol interface assertion groups (AXI handshake, FIFO full/empty, etc.)
  - Covergroup with coverage target ≥ 80%
  - Simulation timeout watchdog
"""

from __future__ import annotations

import re

import httpx

TESTBENCH_SYSTEM = """\
You are an expert FPGA verification engineer generating a SystemVerilog testbench.

The testbench must:
1. Be a self-contained file (includes DUT instantiation, clock/reset generation)
2. Use `timescale 1ns/1ps
3. Generate clock with proper period (default 10ns = 100MHz unless specified)
4. Apply reset for ≥ 5 cycles before starting stimulus
5. Include these test scenarios:
   a. Basic functional test (write/enable → check output)
   b. Boundary conditions (max/min values, simultaneous operations)
   c. Reset test (assert reset mid-operation, verify clean state)
   d. Back-to-back transactions (no gaps, stress test)
6. SVA assertions (at least 1 per interface output signal):
   - Place in `ifdef FORMAL blocks AND always blocks (simulation checks)
   - Protocol assertions for handshake signals
7. Covergroup for all input/output combinations (coverage target ≥ 80%):
   - Coverpoint for each multi-bit port (bins for 0, max, random)
   - Cross coverage between related signals
8. Simulation timeout: $finish after 10x expected run time
9. $display progress markers for each test phase

Output ONLY the complete SystemVerilog testbench code. No explanation.
"""

TESTBENCH_PROMPT = """\
Generate a complete testbench for this module:

Design Intent:
{design_intent_json}

RTL Code:
```systemverilog
{rtl_code}
```

Specification:
{spec}
"""


async def run(
    rtl_code: str,
    spec: str = "",
    design_intent: dict | None = None,
    router_url: str = "http://localhost:8765",
    model: str = "claude",
) -> dict:
    """Generate a coverage-driven testbench.

    Args:
        rtl_code:       The synthesizable RTL code to test.
        spec:           Original natural language spec.
        design_intent:  DesignIntent JSON from Spec2RTL (optional but improves quality).

    Returns:
        {
            "testbench_code": str,
            "coverage_points": int,   # estimated number of coverpoints
            "assertion_count": int,   # number of SVA assertions found
            "warnings":        list,
        }
    """
    import json

    intent_str = json.dumps(design_intent, indent=2) if design_intent else "Not provided"

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{router_url}/chat",
            json={
                "messages": [{
                    "role": "user",
                    "content": TESTBENCH_PROMPT.format(
                        design_intent_json=intent_str,
                        rtl_code=rtl_code[:6000],
                        spec=spec or "No spec provided.",
                    ),
                }],
                "system":      TESTBENCH_SYSTEM,
                "model":       model,
                "temperature": 0.1,
                "max_tokens":  8192,
                "stream":      False,
            },
        )
        resp.raise_for_status()
        raw = resp.json()["content"]

    # Strip markdown fences
    raw = re.sub(r"^```(?:systemverilog|verilog|sv)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
    tb_code = raw.strip()

    # Analyze generated testbench
    assertion_count = len(re.findall(r"\bassert\b", tb_code, re.IGNORECASE))
    coverage_count  = len(re.findall(r"\bcoverpoint\b", tb_code, re.IGNORECASE))

    warnings: list[str] = []
    if assertion_count < 2:
        warnings.append("Low assertion count — consider adding more SVA checks")
    if coverage_count < 1:
        warnings.append("No coverpoints detected — coverage may be incomplete")
    if "`timescale" not in tb_code:
        warnings.append("Missing `timescale directive")

    return {
        "testbench_code":  tb_code,
        "coverage_points": coverage_count,
        "assertion_count": assertion_count,
        "warnings":        warnings,
        "summary": (
            f"TestbenchGen: {assertion_count} assertions, "
            f"{coverage_count} coverpoints generated"
        ),
    }
