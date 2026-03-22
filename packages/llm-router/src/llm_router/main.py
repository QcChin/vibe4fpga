"""LLM Router — unified FastAPI service adapting multiple LLM backends.

Environment variables (set in packages/llm-router/.env):
  ANTHROPIC_API_KEY   — Claude backend
  OPENAI_API_KEY      — OpenAI backend (Phase 2)
  OLLAMA_BASE_URL     — Ollama base URL (default: http://localhost:11434)
  OLLAMA_MODEL        — Ollama model name (default: llama3.2)

Routing strategy:
  model="claude"  → ClaudeAdapter  (code generation, complex reasoning)
  model="ollama"  → OllamaAdapter  (offline / private)
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="FPGA Vibe LLM Router", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Models ────────────────────────────────────────────────────────────────────

class Message(BaseModel):
    role: str       # "user" | "assistant" | "system"
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    model: str = "claude"           # backend key
    skill: str | None = None        # optional skill routing hint
    system: str | None = None       # optional system prompt override
    temperature: float = 0.3
    max_tokens: int = 8192
    stream: bool = True


class ChatResponse(BaseModel):
    content: str
    model_used: str
    tokens_used: int | None = None


# ── Adapter factory ───────────────────────────────────────────────────────────

def _get_adapter(model_key: str):
    """Instantiate the appropriate adapter based on model key."""
    if model_key in ("claude", "claude-sonnet", "claude-opus", "claude-haiku"):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY not set. Add it to packages/llm-router/.env",
            )
        from .adapters.claude import ClaudeAdapter
        return ClaudeAdapter(api_key=api_key, model_key=model_key)

    if model_key == "ollama":
        from .adapters.ollama import OllamaAdapter
        return OllamaAdapter(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        )

    # Phase 4: RTLCoder / CodeV (fine-tuned HDL model via Ollama)
    if model_key in ("rtlcoder", "rtlcoder-7b", "rtlcoder-13b", "codev", "codev-v2", "rtlcoder-q4"):
        from .adapters.rtlcoder import RTLCoderAdapter
        return RTLCoderAdapter(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=model_key,
        )

    raise HTTPException(
        status_code=400,
        detail=f"Unknown model backend: '{model_key}'. Supported: claude, ollama, rtlcoder",
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": "0.1.0",
        "backends": {
            "claude": bool(os.getenv("ANTHROPIC_API_KEY")),
            "ollama": True,  # always try
        },
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Non-streaming chat endpoint."""
    adapter = _get_adapter(req.model)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    chunks: list[str] = []
    async for chunk in adapter.complete(
        messages=messages,
        system=req.system,
        stream=False,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
    ):
        chunks.append(chunk)

    return ChatResponse(content="".join(chunks), model_used=req.model)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE streaming chat endpoint.

    Response format (Server-Sent Events):
      data: {"chunk": "<text>"}
      data: [DONE]
    """
    adapter = _get_adapter(req.model)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    async def generate():
        try:
            async for chunk in adapter.complete(
                messages=messages,
                system=req.system,
                stream=True,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            ):
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Skill endpoints ───────────────────────────────────────────────────────────

class Spec2RTLRequest(BaseModel):
    spec: str
    model: str = "claude"
    project_path: str | None = None


@app.post("/skill/spec2rtl")
async def skill_spec2rtl(req: Spec2RTLRequest) -> dict:
    """Run the Spec2RTL 5-stage pipeline.

    Returns the full Spec2RTLResult as JSON, including:
      rtl_code, score, self_check, declared_decisions, ambiguities_resolved
    """
    from skills.spec2rtl import run as spec2rtl_run

    result = await spec2rtl_run(
        spec=req.spec,
        router_url="http://localhost:8765",
        model=req.model,
        project_path=req.project_path,
    )
    return result.model_dump()


class CodeReviewRequest(BaseModel):
    code: str
    file_name: str = "unknown.v"
    model: str = "claude"


@app.post("/skill/code_review")
async def skill_code_review(req: CodeReviewRequest) -> dict:
    """Run CodeReview skill on provided RTL code."""
    from skills.code_review import review_code

    return await review_code(
        rtl_code=req.code,
        file_name=req.file_name,
        router_url="http://localhost:8765",
        model=req.model,
    )


# ── Phase 2 skill endpoints ───────────────────────────────────────────────────

class WaveformDebugRequest(BaseModel):
    waveform_path: str
    query: str = ""
    model: str = "claude"
    axi_prefix: str = ""


@app.post("/skill/waveform_debug")
async def skill_waveform_debug(req: WaveformDebugRequest) -> dict:
    """Run WaveformDebug: 5 detectors + LLM reasoning (4000-token budget)."""
    from skills.waveform_debug import run as waveform_debug_run

    return await waveform_debug_run(
        waveform_path=req.waveform_path,
        query=req.query,
        router_url="http://localhost:8765",
        model=req.model,
        axi_prefix=req.axi_prefix,
    )


class TimingFixRequest(BaseModel):
    timing_report: str
    rtl_context: str = ""
    model: str = "claude"


@app.post("/skill/timing_fix")
async def skill_timing_fix(req: TimingFixRequest) -> dict:
    """Analyze timing violations and suggest pipeline/MCPath/restructure fixes."""
    from skills.timing_fix import run as timing_fix_run

    return await timing_fix_run(
        timing_report=req.timing_report,
        rtl_context=req.rtl_context,
        router_url="http://localhost:8765",
        model=req.model,
    )


class TestbenchGenRequest(BaseModel):
    rtl_code: str
    spec: str = ""
    design_intent: dict | None = None
    model: str = "claude"


@app.post("/skill/testbench_gen")
async def skill_testbench_gen(req: TestbenchGenRequest) -> dict:
    """Generate coverage-driven testbench (≥80% coverage target, SVA assertions)."""
    from skills.testbench_gen import run as testbench_gen_run

    return await testbench_gen_run(
        rtl_code=req.rtl_code,
        spec=req.spec,
        design_intent=req.design_intent,
        router_url="http://localhost:8765",
        model=req.model,
    )


class VerifyRequest(BaseModel):
    files: list[str]
    top_module: str
    spec: str
    rtl_code: str
    project_path: str
    testbench: str | None = None
    part: str = "xc7a35tcpg236-1"
    simulator: str = "icarus"
    model: str = "claude"
    skip_layers: list[int] = []


@app.post("/skill/verify")
async def skill_verify(req: VerifyRequest) -> dict:
    """Run the 5-layer RTL verification pipeline.

    Returns: score (0-100), verdict (PASS/REVIEW/FAIL), Markdown report,
             per-layer results.
    """
    from skills.verification import run as verify_run

    return await verify_run(
        files=req.files,
        top_module=req.top_module,
        spec=req.spec,
        rtl_code=req.rtl_code,
        project_path=req.project_path,
        testbench=req.testbench,
        part=req.part,
        simulator=req.simulator,
        router_url="http://localhost:8765",
        model=req.model,
        skip_layers=req.skip_layers,
    )


# ── Phase 3 skill endpoints ───────────────────────────────────────────────────

class InstrumentAnalyzeRequest(BaseModel):
    meas_file: str | None = None
    sim_vcd_file: str | None = None
    sim_signal: str = ""
    meas_channel: int = 1
    meas_vendor: str = "auto"
    scpi_resource: str | None = None
    clock_period_ns: float = 10.0
    model: str = "claude"
    instrument_mcp_url: str | None = None


@app.post("/skill/instrument_analyze")
async def skill_instrument_analyze(req: InstrumentAnalyzeRequest) -> dict:
    """Sim-vs-real measurement root cause attribution.

    Aligns simulation VCD with oscilloscope CSV (or live SCPI capture) via
    cross-correlation, classifies differences into expected/suspicious/anomalous
    categories, and performs LLM root-cause attribution.

    Returns:
        alignment, diff_findings, summary, llm_analysis, report_md
    """
    from skills.instrument_analyze import run as instrument_analyze_run

    return await instrument_analyze_run(
        meas_file=req.meas_file,
        sim_vcd_file=req.sim_vcd_file,
        sim_signal=req.sim_signal,
        meas_channel=req.meas_channel,
        meas_vendor=req.meas_vendor,
        scpi_resource=req.scpi_resource,
        clock_period_ns=req.clock_period_ns,
        router_url="http://localhost:8765",
        model=req.model,
        instrument_mcp_url=req.instrument_mcp_url,
    )


# ── Phase 4 skill endpoints ───────────────────────────────────────────────────

class AgentLoopRequest(BaseModel):
    goal:       str
    context:    dict = {}
    model:      str = "claude"
    max_rounds: int = 3
    max_retries_per_step: int = 2


@app.post("/skill/agent_loop")
async def skill_agent_loop(req: AgentLoopRequest) -> dict:
    """Run the autonomous multi-round FPGA design agent loop.

    The agent:
      1. Plans a sequence of steps (spec2rtl, lint, simulate, synthesize, ...)
      2. Executes each step, retrying on transient failures
      3. Re-plans if a round fails, incorporating failure context
      4. Returns a Markdown report with all results

    Returns:
        {goal, success, rounds, completed_steps, failed_steps,
         context, events, report_md, ask_user}
    """
    from skills.agent_loop import run as agent_loop_run

    return await agent_loop_run(
        goal=req.goal,
        context=req.context,
        router_url="http://localhost:8765",
        model=req.model,
        max_rounds=req.max_rounds,
        max_retries_per_step=req.max_retries_per_step,
    )


def start() -> None:
    import uvicorn
    uvicorn.run("llm_router.main:app", host="0.0.0.0", port=8765, reload=True)
