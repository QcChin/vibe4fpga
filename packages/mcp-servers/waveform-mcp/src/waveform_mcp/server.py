"""waveform-mcp — Simulation Waveform Parsing MCP Server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .axi_decoder import decode_axi
from .compressor import compress_for_llm
from .parser import parse_waveform

mcp = FastMCP("waveform-mcp")

# Module-level cache: avoid re-parsing the same file repeatedly
_CACHE: dict[str, object] = {}


def _load(file_path: str):
    if file_path not in _CACHE:
        _CACHE[file_path] = parse_waveform(file_path)
    return _CACHE[file_path]


@mcp.tool()
async def parse_waveform_tool(file_path: str) -> dict:
    """Parse VCD or FST waveform file, return metadata.

    Args:
        file_path: Absolute path to .vcd or .fst file.

    Returns:
        {format, duration_ns, signal_count, clocks, signals}
    """
    meta = _load(file_path)
    return meta.to_summary()


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
    from .compressor import l1_sample

    meta = _load(file_path)
    result: dict[str, list[dict]] = {}

    for name in signals:
        sig = meta.signals.get(name)
        if sig is None:
            result[name] = []
            continue

        # Filter to time window
        tv = sig.tv
        if time_start_ns > 0 or time_end_ns is not None:
            end = time_end_ns or float("inf")
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
    from .compressor import l1_compress_all

    meta = _load(file_path)

    # Filter signals matching the prefix
    relevant = {
        name: sig
        for name, sig in meta.signals.items()
        if name.startswith(axi_prefix) or (clock_name and name == clock_name)
    }

    # Apply time window filter
    if time_start_ns > 0 or time_end_ns is not None:
        end = time_end_ns or float("inf")
        for sig in relevant.values():
            sig.tv = [(t, v) for t, v in sig.tv if time_start_ns <= t <= end]

    compressed = l1_compress_all(relevant)
    return decode_axi(compressed, axi_prefix=axi_prefix, clock_name=clock_name, timeout_cycles=timeout_cycles)


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
    meta = _load(file_path)
    result = compress_for_llm(meta.signals, query=query, token_budget=token_budget)

    # Remove raw signal_events from output (too large), just keep narrative
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
    Post-synthesis mapping requires netlist parsing (Phase 3).

    Args:
        waveform_path: Path to the waveform file.
        signal_name:   Fully-qualified simulation signal name.
        rtl_root:      Optional RTL project root for source search.
    """
    import re
    from pathlib import Path

    if not rtl_root:
        return {"status": "no_rtl_root", "signal": signal_name}

    # Strip scope prefix to get base signal name
    base_name = signal_name.rsplit(".", 1)[-1]
    pattern = re.compile(rf"\b{re.escape(base_name)}\b")

    hits: list[dict] = []
    for fp in Path(rtl_root).rglob("*"):
        if fp.suffix.lower() not in {".v", ".sv"}:
            continue
        try:
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
        "signal":       signal_name,
        "base_name":    base_name,
        "rtl_hits":     hits,
        "status":       "found" if hits else "not_found",
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
