"""AgentLoop — multi-round autonomous FPGA design execution loop.

Loop architecture:
  1. Plan:     LLM decomposes goal → ordered PlanSteps
  2. Execute:  Run each step in dependency order
  3. Evaluate: Check step result; retry or re-plan on failure
  4. Report:   Emit progress events + final Markdown summary

Termination conditions:
  - All steps completed successfully
  - Max rounds reached (configurable, default 3 re-plan attempts)
  - Unrecoverable error (tool not found, auth failure, etc.)
  - ask_user step encountered (pauses loop, returns to human)

Design doc reference: Phase 4 — 多轮Agent自主循环
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import AsyncGenerator

from .executor import StepResult, execute_step
from .planner import AgentPlan, PlanStep, StepType, create_plan


@dataclass
class LoopEvent:
    """Progress event emitted during loop execution."""
    event:     str    # "plan_created" | "step_start" | "step_done" | "step_fail" | "done" | "error"
    timestamp: str    = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    step_id:   int | None = None
    step_type: str | None = None
    message:   str    = ""
    data:      dict   = field(default_factory=dict)


@dataclass
class LoopResult:
    goal:          str
    success:       bool
    rounds:        int
    completed_steps: list[dict]
    failed_steps:  list[dict]
    context:       dict
    events:        list[dict]
    report_md:     str
    ask_user:      str | None = None   # populated if loop paused for human input


# ── Loop state machine ────────────────────────────────────────────────────────

class AgentLoop:
    """Autonomous multi-round execution loop."""

    def __init__(
        self,
        goal: str,
        context: dict,
        router_url: str = "http://localhost:8765",
        model: str = "claude",
        max_rounds: int = 3,
        max_retries_per_step: int = 2,
    ):
        self.goal       = goal
        self.context    = dict(context)
        self.router_url = router_url
        self.model      = model
        self.max_rounds = max_rounds
        self.max_retries_per_step = max_retries_per_step

        self._events:          list[LoopEvent] = []
        self._completed:       set[int] = set()
        self._step_results:    list[StepResult] = []
        self._retry_counts:    dict[int, int] = {}

    def _emit(self, event: str, **kwargs) -> LoopEvent:
        ev = LoopEvent(event=event, **kwargs)
        self._events.append(ev)
        return ev

    async def run(self) -> LoopResult:
        """Execute the full loop. Returns final LoopResult."""
        plan: AgentPlan | None = None
        round_n = 0

        while round_n < self.max_rounds:
            round_n += 1

            # ── Planning phase ────────────────────────────────────────────────
            plan = await create_plan(
                goal=self.goal,
                context=self.context,
                router_url=self.router_url,
                model=self.model,
            )
            self._emit("plan_created",
                       message=f"Round {round_n}: {len(plan.steps)} steps planned",
                       data={"steps": [s.step_id for s in plan.steps]})

            if not plan.steps:
                break

            # ── Execution phase ───────────────────────────────────────────────
            all_success = True
            ask_user_msg: str | None = None

            while True:
                ready = plan.next_ready_steps(self._completed)
                if not ready:
                    break

                # Run ready steps concurrently (if no data dependencies)
                results = await asyncio.gather(
                    *[self._run_step(step, plan) for step in ready]
                )

                for result, step in zip(results, ready):
                    if result is None:
                        continue
                    if step.step_type == StepType.ASK_USER:
                        ask_user_msg = step.params.get("question", "Input required")
                        all_success = False
                        break
                    if result.success:
                        self._completed.add(step.step_id)
                    else:
                        all_success = False

                if ask_user_msg:
                    break

            if ask_user_msg:
                return self._build_result(
                    success=False,
                    rounds=round_n,
                    ask_user=ask_user_msg,
                )

            if all_success:
                break

            # ── Re-plan with failure context ──────────────────────────────────
            failed = [r for r in self._step_results if not r.success]
            self.context["previous_failures"] = [
                {"step_id": r.step_id, "error": r.error, "output": r.output}
                for r in failed[-3:]
            ]

        # ── Build final result ────────────────────────────────────────────────
        all_done = plan is not None and all(
            s.step_id in self._completed for s in plan.steps
        )
        return self._build_result(success=all_done, rounds=round_n)

    async def _run_step(self, step: PlanStep, plan: AgentPlan) -> StepResult | None:
        """Execute one step with retry logic."""
        if step.step_type == StepType.ASK_USER:
            self._emit("step_start", step_id=step.step_id, step_type=step.step_type.value,
                       message=step.description)
            return None   # signal caller to pause

        self._emit("step_start", step_id=step.step_id, step_type=step.step_type.value,
                   message=step.description)

        retries = self._retry_counts.get(step.step_id, 0)
        result  = await execute_step(step, self.context, self.router_url)
        self._step_results.append(result)

        if result.success:
            self._emit("step_done", step_id=step.step_id, step_type=step.step_type.value,
                       message="OK", data={"summary": _summarize_output(result.output)})
        else:
            if result.retry_suggested and retries < self.max_retries_per_step:
                self._retry_counts[step.step_id] = retries + 1
                self._emit("step_fail", step_id=step.step_id, step_type=step.step_type.value,
                           message=f"Failed (retry {retries+1}/{self.max_retries_per_step}): {result.error}")
                return await self._run_step(step, plan)
            else:
                self._emit("step_fail", step_id=step.step_id, step_type=step.step_type.value,
                           message=f"Failed: {result.error}")

        return result

    def _build_result(
        self,
        success: bool,
        rounds: int,
        ask_user: str | None = None,
    ) -> LoopResult:
        completed = [r for r in self._step_results if r.success]
        failed    = [r for r in self._step_results if not r.success]
        report    = _build_report(self.goal, completed, failed, rounds, success)

        return LoopResult(
            goal=self.goal,
            success=success,
            rounds=rounds,
            completed_steps=[{"step_id": r.step_id, "type": r.step_type.value} for r in completed],
            failed_steps=[{"step_id": r.step_id, "type": r.step_type.value, "error": r.error} for r in failed],
            context=self.context,
            events=[asdict(e) for e in self._events],
            report_md=report,
            ask_user=ask_user,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summarize_output(output: dict) -> str:
    """Extract a short summary from step output for event logging."""
    for key in ("summary", "verdict", "overall_verdict", "report_md"):
        if key in output:
            v = output[key]
            return str(v)[:120] if isinstance(v, str) else json.dumps(v)[:120]
    return str(output)[:120]


def _build_report(
    goal: str,
    completed: list[StepResult],
    failed: list[StepResult],
    rounds: int,
    success: bool,
) -> str:
    status = "COMPLETE" if success else "INCOMPLETE"
    lines = [
        f"## Agent Loop Report — {status}",
        "",
        f"**Goal:** {goal}",
        f"**Rounds:** {rounds}  |  "
        f"**Completed:** {len(completed)}  |  "
        f"**Failed:** {len(failed)}",
        "",
    ]

    if completed:
        lines += ["### Completed Steps"]
        for r in completed:
            summary = _summarize_output(r.output)
            lines.append(f"- **{r.step_type.value}** (#{r.step_id}): {summary}")
        lines.append("")

    if failed:
        lines += ["### Failed Steps"]
        for r in failed:
            lines.append(f"- **{r.step_type.value}** (#{r.step_id}): {r.error or 'unknown error'}")
        lines.append("")

    # Extract final RTL code block if available
    for r in reversed(completed):
        rtl = r.output.get("rtl_code", "")
        if rtl:
            lines += ["### Generated RTL", f"```verilog\n{rtl[:1500]}\n```"]
            break

    return "\n".join(lines)


# ── Public entry point ────────────────────────────────────────────────────────

async def run(
    goal: str,
    context: dict | None = None,
    router_url: str = "http://localhost:8765",
    model: str = "claude",
    max_rounds: int = 3,
    max_retries_per_step: int = 2,
) -> dict:
    """Run the autonomous agent loop.

    Args:
        goal:                 High-level design task description.
        context:              Initial context (spec, source_files, toolchain, etc.).
        router_url:           LLM Router URL.
        model:                LLM backend key.
        max_rounds:           Maximum re-planning rounds before giving up.
        max_retries_per_step: How many times to retry a failing step.

    Returns:
        LoopResult as dict: {goal, success, rounds, completed_steps, failed_steps,
                              context, events, report_md, ask_user}
    """
    loop = AgentLoop(
        goal=goal,
        context=context or {},
        router_url=router_url,
        model=model,
        max_rounds=max_rounds,
        max_retries_per_step=max_retries_per_step,
    )
    result = await loop.run()
    return {
        "goal":            result.goal,
        "success":         result.success,
        "rounds":          result.rounds,
        "completed_steps": result.completed_steps,
        "failed_steps":    result.failed_steps,
        "context":         result.context,
        "events":          result.events,
        "report_md":       result.report_md,
        "ask_user":        result.ask_user,
    }
