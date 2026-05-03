"""waveform-mcp — Simulation Waveform Parsing MCP Server.

Exposes:
    Parsing + compression tools (no LLM required):
        parse_waveform_tool, extract_signal_events, decode_axi_tool,
        summarize_for_llm, map_signal_to_rtl
    LLM-backed skill (requires llm-client env setup):
        debug_waveform — waveform_debug skill (5 detectors + LLM reasoning)
"""

from __future__ import annotations

import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .axi_decoder import decode_axi
from .compressor import compress_for_llm, l1_compress_all, l1_sample
from .parser import parse_waveform
from .skills.waveform_debug.skill import run as waveform_debug_run

mcp = FastMCP("waveform-mcp")

# LRU cache: holds at most 8 parsed waveforms (large VCDs can be hundreds of MB)
_CACHE_MAX = 8
_cache: dict[str, object] = {}
_cache_order: list[str] = []   # LRU eviction order


async def _load(file_path: str):
    """Load and cache a waveform file with LRU eviction.

    Async because :func:`parser.parse_waveform` is async (FST → fst2vcd goes
    through a subprocess that must not block the MCP event loop).
    """
    if file_path in _cache:
        # Move to front (most recently used)
        _cache_order.remove(file_path)
        _cache_order.append(file_path)
        return _cache[file_path]

    meta = await parse_waveform(file_path)
    _cache[file_path] = meta
    _cache_order.append(file_path)

    # Evict oldest entry if over limit
    if len(_cache_order) > _CACHE_MAX:
        evict = _cache_order.pop(0)
        _cache.pop(evict, None)

    return meta


@mcp.tool()
async def parse_waveform_tool(file_path: str) -> dict:
    """Parse VCD or FST waveform file, return metadata.

    Args:
        file_path: Absolute path to .vcd or .fst file.

    Returns:
        {format, duration_ns, signal_count, clocks, signals}
    """
    try:
        meta = await _load(file_path)
        return meta.to_summary()
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}"}
    except Exception as exc:
        return {"error": f"Failed to parse waveform: {exc}"}


@mcp.tool()
async def extract_signal_events(
    file_path: str,
    signals: list[str],
    time_start_ns: float = 0.0,
    time_end_ns: float | None = None,
) -> dict[str, list[dict]]:
    """Extract L1-compressed events for specified signals in a time window.

    Args:
        file_path:    Absolute path to waveform file.
        signals:      List of signal names to extract.
        time_start_ns: Window start in nanoseconds.
        time_end_ns:   Window end in nanoseconds (None = end of simulation).

    Returns:
        {signal_name: [{time: float, value: str}]}
    """
    try:
        meta = await _load(file_path)
    except Exception as exc:
        return {"error": f"Failed to load waveform: {exc}"}  # type: ignore[return-value]

    result: dict[str, list[dict]] = {}
    for name in signals:
        sig = meta.signals.get(name)
        if sig is None:
            result[name] = []
            continue

        tv = sig.tv
        if time_start_ns > 0 or time_end_ns is not None:
            end = time_end_ns if time_end_ns is not None else float("inf")
            tv = [(t, v) for t, v in tv if time_start_ns <= t <= end]

        result[name] = l1_sample(tv)

    return result


@mcp.tool()
async def decode_axi_tool(
    file_path: str,
    axi_prefix: str = "",
    clock_name: str | None = None,
    timeout_cycles: int = 100,
    time_start_ns: float = 0.0,
    time_end_ns: float | None = None,
) -> dict:
    """AXI4 5-channel state machine decoder.

    Args:
        file_path:      Absolute path to waveform file.
        axi_prefix:     Signal prefix, e.g. "m_axi_" for m_axi_awvalid etc.
        clock_name:     Clock signal name (auto-detected if None).
        timeout_cycles: Cycles before flagging VALID-without-READY as timeout.

    Returns:
        {transactions, violations, summary}
    """
    try:
        meta = await _load(file_path)
    except Exception as exc:
        return {"error": f"Failed to load waveform: {exc}"}

    relevant = {
        name: sig
        for name, sig in meta.signals.items()
        if name.startswith(axi_prefix) or (clock_name and name == clock_name)
    }

    # Work on copies to avoid mutating the cached WaveformMetadata objects
    if time_start_ns > 0 or time_end_ns is not None:
        end = time_end_ns if time_end_ns is not None else float("inf")
        relevant = {
            name: type(sig)(  # type: ignore[call-arg]
                name=sig.name,
                tv=[(t, v) for t, v in sig.tv if time_start_ns <= t <= end],
                width=getattr(sig, "width", 1),
            )
            for name, sig in relevant.items()
        }

    compressed = l1_compress_all(relevant)
    return decode_axi(compressed, axi_prefix=axi_prefix, clock_name=clock_name,
                      timeout_cycles=timeout_cycles)


@mcp.tool()
async def summarize_for_llm(
    file_path: str,
    query: str = "",
    token_budget: int = 4000,
) -> dict:
    """Full three-level compression → LLM-friendly structured summary.

    Args:
        file_path:    Absolute path to waveform file.
        query:        LLM's question intent (used for L3 query-aware pruning).
        token_budget: Maximum token budget for returned content.

    Returns:
        {metadata_summary, clock_summaries, event_narrative, anomaly_count}
    """
    try:
        meta = await _load(file_path)
    except Exception as exc:
        return {"error": f"Failed to load waveform: {exc}"}

    result = compress_for_llm(meta.signals, query=query, token_budget=token_budget)
    return {
        "metadata_summary": result["metadata_summary"],
        "clock_summaries":  result["clock_summaries"],
        "event_narrative":  result["event_narrative"],
    }


@mcp.tool()
async def map_signal_to_rtl(
    waveform_path: str,
    signal_name: str,
    rtl_root: str | None = None,
) -> dict:
    """Reverse-map a simulation signal name to its RTL source location.

    For pre-synthesis (RTL) simulation: searches RTL files for signal declaration.

    Args:
        waveform_path: Path to the waveform file.
        signal_name:   Fully-qualified simulation signal name.
        rtl_root:      Optional RTL project root for source search.
    """
    if not rtl_root:
        return {"status": "no_rtl_root", "signal": signal_name}

    base_name = signal_name.rsplit(".", 1)[-1]
    pattern = re.compile(rf"\b{re.escape(base_name)}\b")
    max_file_bytes = 512 * 1024   # skip files > 512 KB to avoid reading huge netlists

    hits: list[dict] = []
    for fp in Path(rtl_root).rglob("*"):
        if fp.suffix.lower() not in {".v", ".sv"}:
            continue
        try:
            if fp.stat().st_size > max_file_bytes:
                continue
            for line_no, line in enumerate(fp.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    hits.append({
                        "file":    str(fp),
                        "line":    line_no,
                        "context": line.strip(),
                    })
                    if len(hits) >= 10:
                        break
        except OSError:
            continue
        if len(hits) >= 10:
            break

    return {
        "signal":    signal_name,
        "base_name": base_name,
        "rtl_hits":  hits,
        "status":    "found" if hits else "not_found",
    }


# ═════════════════════════════════════════════════════════════════════════════
# LLM-backed skill — waveform_debug (5 detectors + LLM reasoning)
# ═════════════════════════════════════════════════════════════════════════════

class DebugWaveformInput(BaseModel):
    """Inputs for ``debug_waveform``."""

    vcd_path:     str              = Field(..., description="Absolute path to the VCD (or FST) waveform file to analyze.")
    signals:      list[str] | None = Field(None, description="Optional whitelist of signal names to feed the detectors. ``None`` uses every signal in the trace. Clock signals are always retained so glitch/stall detectors can establish a clock period.")
    query:        str              = Field("",   description="Engineer's question or focus area — drives L3 query-aware pruning of the compressed event narrative and seeds the LLM reasoning prompt.")
    model:        str              = Field("claude", description="LLM model key understood by the vibe4fpga-llm-client registry.")
    axi_prefix:   str              = Field("",   description="AXI signal name prefix (e.g. ``m_axi_``). Empty string skips the AXI violation detector.")
    token_budget: int              = Field(4000, ge=512, le=16000, description="Upper bound on tokens packed into the LLM context (metadata + anomaly list + event narrative).")


@mcp.tool()
async def debug_waveform(inputs: DebugWaveformInput) -> dict:
    """Detect timing anomalies in a VCD waveform and explain root causes.

    Runs 5 pure-Python detectors in parallel (glitches, X/Z states, CDC
    crossings, AXI handshake violations, stall/handshake timeouts), then asks
    the LLM for a root-cause + RTL-fix recommendation per anomaly. All
    waveform parsing and compression happens in-process — no HTTP call out
    to a router or sibling MCP.

    Returns:
        ``{anomaly_count, error_count, warning_count, anomalies, llm_analysis, summary}``.
    """
    return await waveform_debug_run(
        waveform_path=inputs.vcd_path,
        query=inputs.query,
        model=inputs.model,
        axi_prefix=inputs.axi_prefix,
        token_budget=inputs.token_budget,
        signals=inputs.signals,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Console-script entry point for ``waveform-mcp``."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
