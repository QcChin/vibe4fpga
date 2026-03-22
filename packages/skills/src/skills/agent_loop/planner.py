"""Agent planner — decomposes high-level goals into executable step sequences.

The planner uses an LLM to break complex FPGA design tasks into a concrete
ordered list of actions, each mapped to a skill or MCP tool call.

Available actions (step types):
  spec2rtl        — Generate RTL from specification
  code_review     — Review existing RTL code
  lint            — Run Verilator/Verible lint
  simulate        — Icarus/Vivado simulation
  timing_fix      — Analyze and fix timing violations
  synthesize      — Run synthesis (Vivado/Quartus/Yosys)
  formal_verify   — Run SymbiYosys formal verification
  waveform_debug  — Analyze VCD waveform
  instrument_analyze — Compare sim vs. real measurement
  program         — Flash bitstream to device
  ask_user        — Pause and request human input

Design doc reference: Phase 4 — 多轮Agent自主循环
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


class StepType(str, Enum):
    SPEC2RTL            = "spec2rtl"
    CODE_REVIEW         = "code_review"
    LINT                = "lint"
    SIMULATE            = "simulate"
    TIMING_FIX          = "timing_fix"
    SYNTHESIZE          = "synthesize"
    FORMAL_VERIFY       = "formal_verify"
    WAVEFORM_DEBUG      = "waveform_debug"
    INSTRUMENT_ANALYZE  = "instrument_analyze"
    PROGRAM             = "program"
    ASK_USER            = "ask_user"


@dataclass
class PlanStep:
    step_id:    int
    step_type:  StepType
    description: str
    params:     dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)   # step_ids that must complete first


@dataclass
class AgentPlan:
    goal:       str
    steps:      list[PlanStep]
    context:    dict = field(default_factory=dict)   # shared state across steps

    def next_ready_steps(self, completed: set[int]) -> list[PlanStep]:
        """Return steps whose dependencies are all satisfied."""
        return [
            s for s in self.steps
            if s.step_id not in completed
            and all(dep in completed for dep in s.depends_on)
        ]


# ── Prompts ───────────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """\
You are an autonomous FPGA design agent planner.

Given a high-level design goal, produce a JSON execution plan with ordered steps.

Available step types:
  spec2rtl, code_review, lint, simulate, timing_fix, synthesize,
  formal_verify, waveform_debug, instrument_analyze, program, ask_user

Output JSON:
{
  "goal": "string",
  "steps": [
    {
      "step_id":    1,
      "step_type":  "spec2rtl",
      "description": "Generate RTL from the UART spec",
      "params": {"spec": "...", "model": "claude"},
      "depends_on": []
    },
    ...
  ]
}

Rules:
  - lint always depends on spec2rtl (if RTL was generated)
  - simulate depends on lint passing
  - timing_fix depends on synthesize
  - program depends on synthesize
  - Keep plans concise (3-8 steps for most tasks)
  - Use ask_user only when genuinely ambiguous requirements block progress
"""

PLANNER_USER = """\
Design goal: {goal}

Context:
{context_json}

Generate a step-by-step execution plan.
"""


# ── Planner ───────────────────────────────────────────────────────────────────

async def create_plan(
    goal: str,
    context: dict,
    router_url: str = "http://localhost:8765",
    model: str = "claude",
) -> AgentPlan:
    """Call LLM to produce an AgentPlan for the given goal."""
    import httpx

    prompt = PLANNER_USER.format(
        goal=goal,
        context_json=json.dumps(context, indent=2)[:2000],
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{router_url}/chat",
            json={
                "messages":    [{"role": "user", "content": prompt}],
                "system":      PLANNER_SYSTEM,
                "model":       model,
                "temperature": 0.1,
                "stream":      False,
            },
        )

    if resp.status_code != 200:
        # Fallback: single spec2rtl step
        return AgentPlan(
            goal=goal,
            steps=[PlanStep(
                step_id=1,
                step_type=StepType.SPEC2RTL,
                description="Generate RTL from specification",
                params={"spec": goal},
            )],
            context=context,
        )

    raw = resp.json().get("content", "")
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return AgentPlan(goal=goal, steps=[], context=context)

    steps = []
    for s in data.get("steps", []):
        try:
            steps.append(PlanStep(
                step_id=s["step_id"],
                step_type=StepType(s["step_type"]),
                description=s.get("description", ""),
                params=s.get("params", {}),
                depends_on=s.get("depends_on", []),
            ))
        except (KeyError, ValueError):
            continue

    return AgentPlan(goal=data.get("goal", goal), steps=steps, context=context)
