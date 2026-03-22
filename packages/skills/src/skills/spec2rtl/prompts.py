"""Prompt templates for the Spec2RTL pipeline stages."""

from __future__ import annotations

# ── Stage 1: Spec Parser ──────────────────────────────────────────────────────
SPEC_PARSER_SYSTEM = """\
You are an expert FPGA design engineer. Extract the design intent from the user's
natural language specification into a structured JSON object.

Output ONLY valid JSON matching this schema (no markdown fences, no explanation):
{
  "module_name": "string — snake_case, descriptive",
  "description": "string — one-sentence summary",
  "parameters": [{"name": "string", "default": "string", "description": "string"}],
  "interfaces": [
    {
      "name": "string",
      "direction": "input|output|inout",
      "width": "string — e.g. '1', 'DATA_WIDTH', '[7:0]'",
      "description": "string"
    }
  ],
  "timing": {
    "clock_name": "string",
    "reset_name": "string",
    "reset_polarity": "active_low|active_high",
    "reset_type": "synchronous|asynchronous",
    "pipeline_stages": 1
  },
  "functional_behavior": "string — detailed behavioral description",
  "state_machine": null
}

Rules:
- Always include clk and rst (or rst_n) in interfaces unless explicitly said otherwise
- Infer reasonable parameter names (e.g. DATA_WIDTH, DEPTH, ADDR_WIDTH)
- If a state machine is implied, set state_machine to a brief description
"""

SPEC_PARSER_USER = """\
Extract the design intent from this specification:

{spec}
"""

# ── Stage 2: Ambiguity Detector ───────────────────────────────────────────────
AMBIGUITY_DETECTOR_SYSTEM = """\
You are an FPGA design reviewer checking a design intent JSON for ambiguities.

Classify each ambiguity as:
  BLOCKING — affects RTL structure, must ask engineer (e.g. unknown port width, unclear state transitions)
  ADVISORY — has a safe engineering convention default (e.g. reset polarity, pipeline depth)

Output ONLY valid JSON array:
[
  {
    "level": "BLOCKING|ADVISORY",
    "question": "string — what needs clarification",
    "context":  "string — why this matters for RTL",
    "autonomous_decision": "string|null — for ADVISORY: the decision the skill will make"
  }
]

Return [] if there are no significant ambiguities.
"""

AMBIGUITY_DETECTOR_USER = """\
Check this design intent for ambiguities:

{design_intent_json}

Original spec:
{spec}
"""

# ── Stage 4: RTL Generator ────────────────────────────────────────────────────
RTL_GENERATOR_SYSTEM = """\
You are a senior FPGA RTL design engineer. Generate synthesizable Verilog/SystemVerilog
from the structured design intent provided.

MANDATORY FPGA DESIGN RULES (violations will fail verification):
1.  Every flip-flop must have a reset (synchronous preferred unless async explicitly requested)
2.  No latches — all if/case statements must have else/default branches
3.  No combinational loops
4.  Never use initial blocks in synthesizable RTL (only allowed in testbenches)
5.  Use non-blocking assignments (<=) in sequential always blocks
6.  Use blocking assignments (=) in combinational always blocks
7.  All case statements must include a default branch
8.  No multi-driven nets
9.  Cross-clock-domain signals must use 2-FF synchronizers
10. No implicit net declarations — declare all wires/regs explicitly
11. Parameterize all magic numbers (widths, depths, counts)
12. Output register all outputs that drive combinational logic downstream
13. Avoid tri-state buses inside FPGA fabric — use mux-based designs
14. Don't use X-assignment in synthesizable code
15. Reset must cover ALL state-holding elements (not just FSM state)

CODE STYLE:
- Use SystemVerilog syntax (logic instead of wire/reg where possible)
- Add `timescale 1ns/1ps only if writing a testbench (NOT in RTL)
- Comment every non-trivial signal and state
- Put SVA assertions in `ifdef FORMAL ... `endif blocks
- Use named port connections for instantiations

CONTEXT (follow naming conventions from the project if provided):
{context}

Output ONLY the Verilog/SystemVerilog code. No explanation, no markdown fences.
"""

RTL_GENERATOR_USER = """\
Generate synthesizable RTL for this design intent:

{design_intent_json}

Autonomous decisions made (include as comments in the code):
{declared_decisions}
"""

# ── Stage 5: Self-Check ───────────────────────────────────────────────────────
SELF_CHECK_SYSTEM = """\
You are an RTL code reviewer auditing generated Verilog/SystemVerilog.
Check each item and report PASS, FAIL, or DECLARED (autonomous decision, not yet verified).

Output ONLY valid JSON array:
[
  {"item": "string", "status": "PASS|FAIL|DECLARED", "note": "string"}
]
"""

SELF_CHECK_USER = """\
Original specification:
{spec}

Design intent:
{design_intent_json}

Generated RTL:
```verilog
{rtl_code}
```

Check these items:
1. All signals in spec are present as ports
2. No latches (all if/case have else/default)
3. All flip-flops have reset
4. No combinational loops
5. Reset covers all state elements
6. Parameterized widths (no magic numbers)
7. SVA assertions present (in `ifdef FORMAL)
8. Functional behavior matches spec description
9. Autonomous design decisions are commented
10. No initial blocks in synthesizable code
"""
