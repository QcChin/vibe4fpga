"""Three-level waveform compression for LLM context budget management.

L1 — Activity-aware sampling:
     Preserve all edge-dense regions; for stable plateaus keep only boundaries.
     Goal: 10-50x size reduction while retaining all timing information.

L2 — Semantic extraction:
     Clocks → frequency/phase summary; buses → transaction sequences.
     Goal: Replace raw waveforms with human-readable descriptions.

L3 — Problem-oriented pruning:
     Combine with LLM query intent to prioritize relevant signal windows.
     Goal: Fit within token budget while keeping anomaly-relevant data.
"""

from __future__ import annotations

import re
from typing import Any


# ── L1: Activity-aware sampling ───────────────────────────────────────────────

def l1_sample(
    tv: list[tuple[float, str]],
    stable_window_threshold: int = 3,
) -> list[dict]:
    """Activity-aware sampling of a signal's time-value pairs.

    Keeps all transitions (edges). For stable regions (same value for many
    consecutive samples), keeps only the first and last sample.

    Args:
        tv:                      List of (time_ns, value_str) pairs.
        stable_window_threshold: Consecutive stable samples before compression.

    Returns:
        Compressed list of {"time": float, "value": str} dicts.
    """
    if len(tv) <= 2:
        return [{"time": t, "value": v} for t, v in tv]

    result: list[dict] = []
    result.append({"time": tv[0][0], "value": tv[0][1]})

    stable_count = 0
    stable_start_idx = 0

    for i in range(1, len(tv)):
        cur_val = tv[i][1]
        prev_val = tv[i - 1][1]

        if cur_val != prev_val:
            # Edge detected
            if stable_count > stable_window_threshold:
                # Close the stable region: include the last stable sample
                result.append({"time": tv[i - 1][0], "value": tv[i - 1][1]})
            result.append({"time": tv[i][0], "value": cur_val})
            stable_count = 0
            stable_start_idx = i
        else:
            stable_count += 1

    # Ensure last sample is included
    if result[-1]["time"] != tv[-1][0]:
        result.append({"time": tv[-1][0], "value": tv[-1][1]})

    return result


def l1_compress_all(
    signals: dict[str, Any],  # name → SignalTrace
    max_events_per_signal: int = 200,
) -> dict[str, list[dict]]:
    """Apply L1 compression to all signals."""
    result: dict[str, list[dict]] = {}
    for name, sig in signals.items():
        compressed = l1_sample(sig.tv)
        # Further cap per signal to max_events_per_signal
        if len(compressed) > max_events_per_signal:
            # Keep first + last + evenly spaced sample
            step = len(compressed) // max_events_per_signal
            compressed = (
                compressed[:1]
                + compressed[1:-1:step]
                + compressed[-1:]
            )
        result[name] = compressed
    return result


# ── L2: Semantic extraction ───────────────────────────────────────────────────

def l2_extract_clock_summary(sig_name: str, tv_ns: list[dict]) -> str:
    """Convert clock signal to frequency/phase description."""
    if len(tv_ns) < 4:
        return f"{sig_name}: insufficient data for clock analysis"

    edges = [e for e in tv_ns if e["value"] in ("0", "1")]
    rising = [e["time"] for e in edges if e["value"] == "1"]
    if len(rising) < 2:
        return f"{sig_name}: no rising edges detected"

    periods = [rising[i + 1] - rising[i] for i in range(min(10, len(rising) - 1))]
    mean_period = sum(periods) / len(periods)
    freq_mhz = 1000.0 / mean_period if mean_period > 0 else 0

    return (
        f"{sig_name}: {freq_mhz:.2f} MHz (period {mean_period:.2f} ns), "
        f"{len(rising)} rising edges in {tv_ns[-1]['time'] - tv_ns[0]['time']:.0f} ns"
    )


def l2_extract_bus_summary(sig_name: str, tv_ns: list[dict]) -> str:
    """Convert a bus signal to value-change sequence description."""
    if not tv_ns:
        return f"{sig_name}: no data"

    changes = [(e["time"], e["value"]) for e in tv_ns if e["value"] not in ("x", "z")]
    if not changes:
        return f"{sig_name}: all X/Z"

    # Summarize first and last N unique values
    unique_vals = list(dict.fromkeys(v for _, v in changes))
    preview = unique_vals[:5]
    suffix = f"... ({len(unique_vals)} unique values)" if len(unique_vals) > 5 else ""

    return f"{sig_name}: {', '.join(preview)}{suffix} over {changes[-1][0] - changes[0][0]:.0f} ns"


def l2_describe_signals(
    signals: dict[str, Any],   # name → SignalTrace
    compressed: dict[str, list[dict]],
) -> dict[str, str]:
    """Generate L2 semantic descriptions for all signals."""
    descriptions: dict[str, str] = {}
    for name, sig in signals.items():
        tv = compressed.get(name, [])
        if sig.is_clock:
            descriptions[name] = l2_extract_clock_summary(name, tv)
        elif sig.width > 1:
            descriptions[name] = l2_extract_bus_summary(name, tv)
        # Single-bit non-clock: leave for event narrative
    return descriptions


# ── L3: Problem-oriented pruning ──────────────────────────────────────────────

def l3_prune(
    compressed: dict[str, list[dict]],
    query_keywords: list[str],
    token_budget: int = 3000,
    chars_per_token: int = 4,
) -> dict[str, list[dict]]:
    """Prioritize signals and time windows relevant to the LLM query.

    Signals matching query keywords get higher priority.
    All signals are included until the character budget is exhausted.
    """
    char_budget = token_budget * chars_per_token

    # Score signals by keyword relevance
    scored: list[tuple[int, str, list[dict]]] = []
    for name, events in compressed.items():
        score = sum(kw.lower() in name.lower() for kw in query_keywords)
        scored.append((score, name, events))

    scored.sort(key=lambda x: -x[0])  # higher score first

    result: dict[str, list[dict]] = {}
    used_chars = 0

    for _, name, events in scored:
        serialized = str(events)
        if used_chars + len(serialized) > char_budget:
            # Trim events to fit remaining budget
            remaining = char_budget - used_chars
            max_events = max(1, remaining // 40)  # ~40 chars/event
            result[name] = events[:max_events]
            break
        result[name] = events
        used_chars += len(serialized)

    return result


# ── Full pipeline ─────────────────────────────────────────────────────────────

def compress_for_llm(
    signals: dict[str, Any],   # name → SignalTrace
    query: str = "",
    token_budget: int = 4000,
) -> dict:
    """Full three-level compression → LLM-ready structured output.

    Returns:
        {
            "metadata_summary":   str,    # ~150 tokens
            "clock_summaries":    [str],  # L2 clock descriptions
            "event_narrative":    str,    # L1+L3 compressed events as text
            "signal_events":      dict,   # raw compressed events for anomaly detectors
        }
    """
    # L1
    compressed = l1_compress_all(signals)

    # L2 semantic descriptions
    descriptions = l2_describe_signals(signals, compressed)
    clock_summaries = [
        desc for name, desc in descriptions.items()
        if signals[name].is_clock
    ]

    # L3: extract keywords from query for pruning
    keywords = re.findall(r"\w+", query.lower()) if query else []
    # Always include non-clock signals in L3 pruning
    non_clock = {n: evts for n, evts in compressed.items() if not signals[n].is_clock}
    pruned = l3_prune(non_clock, keywords, token_budget=token_budget - 400)

    # Build event narrative (human-readable)
    narrative_lines: list[str] = []
    for name, events in pruned.items():
        if not events:
            continue
        sig = signals[name]
        if sig.width == 1:
            # Single-bit: describe transitions
            transitions = [
                f"t={e['time']:.1f}ns: {'↑' if e['value'] == '1' else '↓' if e['value'] == '0' else e['value']}"
                for e in events
            ]
            narrative_lines.append(f"{name}: {', '.join(transitions[:20])}")
        else:
            narrative_lines.append(l2_extract_bus_summary(name, events))

    return {
        "metadata_summary": (
            f"Duration: {signals and max(sig.tv[-1][0] for sig in signals.values() if sig.tv) or 0:.0f} ns | "
            f"Signals: {len(signals)} | "
            f"Clocks: {sum(1 for s in signals.values() if s.is_clock)}"
        ),
        "clock_summaries":  clock_summaries,
        "event_narrative":  "\n".join(narrative_lines),
        "signal_events":    compressed,   # kept for detector use
    }
