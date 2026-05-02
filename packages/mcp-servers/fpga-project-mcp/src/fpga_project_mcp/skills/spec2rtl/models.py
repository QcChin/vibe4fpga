"""Pydantic models for the Spec2RTL pipeline."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class InterfaceSignal(BaseModel):
    name: str
    direction: str       # "input" | "output" | "inout"
    width: str = "1"     # e.g. "8", "DATA_WIDTH", "[7:0]"
    description: str = ""


class TimingConstraint(BaseModel):
    clock_name: str = "clk"
    reset_name: str = "rst_n"
    reset_polarity: str = "active_low"   # "active_low" | "active_high"
    reset_type: str = "synchronous"      # "synchronous" | "asynchronous"
    pipeline_stages: int = 1


class DesignIntent(BaseModel):
    """Structured design intent extracted from natural language spec (Stage 1 output)."""
    module_name: str
    description: str
    parameters: list[dict] = Field(default_factory=list)
    interfaces: list[InterfaceSignal] = Field(default_factory=list)
    timing: TimingConstraint = Field(default_factory=TimingConstraint)
    functional_behavior: str = ""
    state_machine: dict | None = None   # FSM description if applicable
    raw_spec: str = ""


class AmbiguityLevel(str, Enum):
    BLOCKING = "BLOCKING"   # must ask engineer before proceeding
    ADVISORY = "ADVISORY"   # skill decides autonomously, records decision


class AmbiguityItem(BaseModel):
    level: AmbiguityLevel
    question: str
    context: str
    autonomous_decision: str | None = None  # filled for ADVISORY items


class SelfCheckResult(BaseModel):
    item: str
    status: str   # "PASS" | "FAIL" | "DECLARED"
    note: str = ""


class Spec2RTLResult(BaseModel):
    """Final output of the Spec2RTL pipeline."""
    module_name: str
    rtl_code: str
    design_intent: DesignIntent
    ambiguities_resolved: list[AmbiguityItem] = Field(default_factory=list)
    self_check: list[SelfCheckResult] = Field(default_factory=list)
    score: float = 100.0
    passed: bool = True
    declared_decisions: list[str] = Field(default_factory=list)
