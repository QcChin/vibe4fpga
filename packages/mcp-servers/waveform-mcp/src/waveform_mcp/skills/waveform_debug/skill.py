"""WaveformDebug Skill — 5 parallel detectors + LLM reasoning.

Token budget management (strictly within 4000 tokens total):
  Metadata summary:   ~150 tokens  (always included)
  Event narrative:    ~300 tokens per anomaly
  RTL code snippet:   ~400 tokens per anomaly
  Budget consumed:    min(anomaly_count × 700, 3800) + 150 metadata

Post-pivot (v0.2.0): LLM calls go through ``vibe4fpga-llm-client`` via the
shared ``_llm.py`` helper instead of the retired FastAPI router. Waveform
data is parsed in-process using this package's own parser + compressor —
no out-of-process ``waveform-mcp`` HTTP calls.
"""

from __future__ import annotations

import json

from .._llm import call_llm, parse_json_response
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


def _detect_clock(signal_events: dict[str, list[dict]]) -> tuple[float, list[str]]:
    """Infer clock period and clock-signal names from compressed events.

    Returns:
        (clock_period_ns, clock_names). Default 10 ns (100 MHz) when no
        plausible clock is visible in the trace.
    """
    clock_period_ns = 10.0
    clock_names: list[str] = []
    for name, evts in signal_events.items():
        if "clk" not in name.lower():
            continue
        if not isinstance(evts, list) or len(evts) <= 10:
            continue
        rising = [
            e["time"]
            for i, e in enumerate(evts[1:], 1)
            if evts[i - 1]["value"] in ("0", "x") and e["value"] == "1"
        ]
        if len(rising) >= 2:
            clock_period_ns = (rising[-1] - rising[0]) / (len(rising) - 1)
            clock_names.append(name)
    return clock_period_ns, clock_names


async def run(
    waveform_path: str,
    query:         str = "",
    model:         str = "claude",
    axi_prefix:    str = "",
    token_budget:  int = WAVEFORM_DEBUG_TOKEN_BUDGET,
    signals:       list[str] | None = None,
) -> dict:
    """Run the WaveformDebug skill.

    Args:
        waveform_path: Path to .vcd or .fst file.
        query:         Engineer's question / focus area for L3 pruning.
        model:         LLM model key understood by vibe4fpga-llm-client.
        axi_prefix:    AXI signal prefix if an AXI bus is present.
        token_budget:  Max tokens for the LLM context bundle.
        signals:       Optional whitelist of signals to feed the detectors.
                       ``None`` means all signals in the trace.

    Returns:
        {
            "anomaly_count":   int,
            "error_count":     int,
            "warning_count":   int,
            "anomalies":       [Anomaly as dict],
            "llm_analysis":    [{"anomaly_index", "root_cause", "fix", "severity"}],
            "summary":         str,
        }
    """
    metadata_summary = ""
    event_narrative  = ""
    signal_events: dict[str, list[dict]] = {}

    # Parse + compress in-process. No subprocess, no /tmp, no HTTP.
    try:
        from waveform_mcp.compressor import compress_for_llm
        from waveform_mcp.parser import parse_waveform

        meta = await parse_waveform(waveform_path)
        all_signals = meta.signals
        # Optional signal whitelist — keeps the compressor bounded on wide traces.
        if signals:
            filtered = {n: s for n, s in all_signals.items() if n in set(signals)}
            # Always preserve clocks so detectors can establish a clock period.
            for name, sig in all_signals.items():
                if sig.is_clock:
                    filtered.setdefault(name, sig)
            all_signals = filtered or all_signals

        compressed = compress_for_llm(all_signals, query=query, token_budget=token_budget)
        metadata_summary = compressed["metadata_summary"]
        event_narrative  = compressed["event_narrative"]
        signal_events    = compressed["signal_events"]
    except Exception as exc:   # noqa: BLE001 — surface as a skill-level error
        metadata_summary = f"Failed to parse {waveform_path}: {exc}"

    # Clock info for the glitch / handshake-timeout detectors.
    clock_period_ns, clock_names = _detect_clock(signal_events)

    # Run 5 detectors in parallel — all deterministic pure-Python logic.
    anomalies = await run_all_detectors(
        signal_events=signal_events,
        clock_period_ns=clock_period_ns,
        clock_names=clock_names,
        axi_prefix=axi_prefix,
    )

    context, anomaly_dicts = _build_llm_context(
        metadata_summary, event_narrative, anomalies, token_budget
    )

    llm_analysis: list[dict] = []
    if anomalies:
        user_prompt = (
            f"{context}\n\nEngineer's question: {query or 'Analyze all anomalies.'}"
        )
        raw = await call_llm(
            messages=[{"role": "user", "content": user_prompt}],
            system=WAVEFORM_DEBUG_SYSTEM,
            model=model,
            temperature=0.2,
        )
        try:
            llm_analysis = parse_json_response(raw)
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
