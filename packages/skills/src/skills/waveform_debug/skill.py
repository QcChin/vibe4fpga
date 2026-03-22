"""WaveformDebug Skill — 5 parallel detectors + LLM reasoning.

Token budget management (strictly within 4000 tokens total):
  Metadata summary:   ~150 tokens  (always included)
  Event narrative:    ~300 tokens per anomaly
  RTL code snippet:   ~400 tokens per anomaly
  Budget consumed:    min(anomaly_count × 700, 3800) + 150 metadata
"""

from __future__ import annotations

import json

import httpx

from .detectors import Anomaly, run_all_detectors

WAVEFORM_DEBUG_SYSTEM = """\
You are an FPGA debug engineer analyzing simulation waveform anomalies.

Given:
  1. Simulation metadata (duration, clocks, signal count)
  2. Detected anomalies (from automated detectors)
  3. Compressed waveform events (edge narrative)

For each anomaly, provide:
  - Root cause explanation (based on FPGA/digital design principles)
  - Specific RTL fix recommendation
  - Whether the issue is critical (blocks functionality) or advisory

Output as JSON array:
[
  {
    "anomaly_index": int,
    "root_cause":    "string — technical explanation",
    "fix":           "string — specific RTL change to make",
    "severity":      "critical|major|minor",
    "confidence":    "high|medium|low"
  }
]
"""

WAVEFORM_DEBUG_TOKEN_BUDGET = 4000
CHARS_PER_TOKEN = 4
METADATA_TOKENS = 150
TOKENS_PER_ANOMALY = 700   # 300 event + 400 context


def _build_llm_context(
    metadata_summary: str,
    event_narrative: str,
    anomalies: list[Anomaly],
    token_budget: int = WAVEFORM_DEBUG_TOKEN_BUDGET,
) -> tuple[str, list[dict]]:
    """Build the LLM context payload, strictly within token_budget.

    Returns:
        (context_text, serialized_anomalies_for_payload)
    """
    char_budget = token_budget * CHARS_PER_TOKEN

    # Layer 1: metadata (always included)
    context_parts = [f"## Simulation Metadata\n{metadata_summary}"]
    used = METADATA_TOKENS * CHARS_PER_TOKEN

    # Layer 2: anomaly details (highest severity first)
    anomaly_dicts = []
    for i, anomaly in enumerate(anomalies):
        anomaly_text = (
            f"\n### Anomaly {i}: [{anomaly.severity.upper()}] {anomaly.anomaly_type}\n"
            f"Signal: {anomaly.signal} @ t={anomaly.time_ns:.1f} ns\n"
            f"Detector: {anomaly.detector}\n"
            f"Message: {anomaly.message}\n"
            f"RTL hint: {anomaly.rtl_hint}\n"
        )
        if used + len(anomaly_text) > char_budget:
            break  # stop adding anomalies when budget exhausted
        context_parts.append(anomaly_text)
        used += len(anomaly_text)
        anomaly_dicts.append({
            "index":       i,
            "detector":    anomaly.detector,
            "severity":    anomaly.severity,
            "signal":      anomaly.signal,
            "time_ns":     anomaly.time_ns,
            "type":        anomaly.anomaly_type,
            "message":     anomaly.message,
        })

    # Layer 3: compressed waveform events (if budget allows)
    remaining = char_budget - used
    if remaining > 200 and event_narrative:
        truncated = event_narrative[: remaining - 100]
        context_parts.append(f"\n## Waveform Events (compressed)\n{truncated}")

    return "\n".join(context_parts), anomaly_dicts


async def run(
    waveform_path: str,
    query: str = "",
    router_url: str = "http://localhost:8765",
    model: str = "claude",
    waveform_mcp_url: str | None = None,
    axi_prefix: str = "",
    token_budget: int = WAVEFORM_DEBUG_TOKEN_BUDGET,
) -> dict:
    """Run the WaveformDebug skill.

    Args:
        waveform_path:    Path to .vcd or .fst file.
        query:            Engineer's question/focus area.
        router_url:       LLM Router URL.
        model:            LLM backend.
        waveform_mcp_url: waveform-mcp URL for fetching compressed data.
        axi_prefix:       AXI signal prefix if AXI bus present.
        token_budget:     Max tokens for LLM context.

    Returns:
        {
            "anomaly_count":    int,
            "anomalies":        [Anomaly as dict],
            "llm_analysis":     [{"anomaly_index", "root_cause", "fix", "severity"}],
            "summary":          str,
        }
    """
    # Fetch compressed waveform data from waveform-mcp (or parse directly)
    metadata_summary = ""
    event_narrative  = ""
    signal_events: dict = {}

    if waveform_mcp_url:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Get summary
            r1 = await client.post(
                f"{waveform_mcp_url}/tools/summarize_for_llm",
                json={"file_path": waveform_path, "query": query, "token_budget": token_budget},
            )
            if r1.status_code == 200:
                data = r1.json()
                metadata_summary = data.get("metadata_summary", "")
                event_narrative  = data.get("event_narrative", "")

            # Get raw compressed events for detectors
            r2 = await client.post(
                f"{waveform_mcp_url}/tools/parse_waveform_tool",
                json={"file_path": waveform_path},
            )
            if r2.status_code == 200:
                meta = r2.json()
                signal_events = {
                    s["name"]: []  # events will be fetched per-signal as needed
                    for s in meta.get("signals", [])
                }
    else:
        # Direct parse (when not using MCP server)
        try:
            from waveform_mcp.compressor import compress_for_llm
            from waveform_mcp.parser import parse_waveform

            meta = parse_waveform(waveform_path)
            compressed = compress_for_llm(meta.signals, query=query, token_budget=token_budget)
            metadata_summary = compressed["metadata_summary"]
            event_narrative  = compressed["event_narrative"]
            signal_events    = compressed["signal_events"]
        except Exception as exc:
            metadata_summary = f"Failed to parse {waveform_path}: {exc}"

    # Detect clock info for detectors
    clock_period_ns = 10.0  # default 100 MHz
    clock_names: list[str] = []
    if signal_events:
        try:
            from waveform_mcp.parser import SignalTrace
            for name, evts in signal_events.items():
                if "clk" in name.lower() and isinstance(evts, list) and len(evts) > 10:
                    rising = [e["time"] for i, e in enumerate(evts[1:], 1)
                              if evts[i-1]["value"] in ("0","x") and e["value"] == "1"]
                    if len(rising) >= 2:
                        clock_period_ns = (rising[-1] - rising[0]) / (len(rising) - 1)
                        clock_names.append(name)
        except Exception:
            pass

    # Run 5 detectors in parallel
    anomalies = await run_all_detectors(
        signal_events=signal_events,
        clock_period_ns=clock_period_ns,
        clock_names=clock_names,
        axi_prefix=axi_prefix,
    )

    # Build LLM context
    context, anomaly_dicts = _build_llm_context(
        metadata_summary, event_narrative, anomalies, token_budget
    )

    llm_analysis: list[dict] = []
    if anomalies:
        user_prompt = f"{context}\n\nEngineer's question: {query or 'Analyze all anomalies.'}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{router_url}/chat",
                json={
                    "messages":    [{"role": "user", "content": user_prompt}],
                    "system":      WAVEFORM_DEBUG_SYSTEM,
                    "model":       model,
                    "temperature": 0.2,
                    "stream":      False,
                },
            )
            if resp.status_code == 200:
                raw = resp.json()["content"]
                import re
                raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
                raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
                try:
                    llm_analysis = json.loads(raw.strip())
                except json.JSONDecodeError:
                    llm_analysis = [{"raw_response": raw}]

    error_count   = sum(1 for a in anomalies if a.severity == "error")
    warning_count = sum(1 for a in anomalies if a.severity == "warning")

    return {
        "anomaly_count":  len(anomalies),
        "error_count":    error_count,
        "warning_count":  warning_count,
        "anomalies":      anomaly_dicts,
        "llm_analysis":   llm_analysis,
        "summary": (
            f"WaveformDebug: {error_count} error(s), {warning_count} warning(s) "
            f"detected in {waveform_path}"
        ),
    }
