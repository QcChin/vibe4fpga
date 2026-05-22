//! L1/L2/L3 waveform compression for LLM context budgets.
//!
//! L1: activity-aware sampling (preserve edges, collapse stable runs).
//! L2: semantic extraction (clock frequencies, bus value sequences).
//! L3: query-aware pruning (prioritize signals matching engineer keywords).
//!
//! Output of `compress_for_llm` matches the Python `compressor.py` JSON shape
//! so the rest of `debug_waveform` can consume it unchanged.

use crate::parser::WaveformMeta;
use serde_json::{json, Map, Value};
use std::collections::HashMap;

/// Maximum events returned per signal before sampling kicks in.
const MAX_EVENTS: usize = 500;

/// Stable-run threshold: compress consecutive identical values longer than this.
const STABLE_RUN_THRESHOLD: usize = 8;

/// L1 compression: preserve all transitions, collapse stable runs.
pub fn l1_sample(tv: &[(f64, String)]) -> Vec<Value> {
    if tv.is_empty() {
        return Vec::new();
    }
    if tv.len() <= MAX_EVENTS {
        return tv
            .iter()
            .map(|(t, v)| json!({"time": t, "value": v}))
            .collect();
    }

    let mut result: Vec<Value> = Vec::new();
    let mut run_start_idx: usize = 0;

    let mut i = 1usize;
    while i <= tv.len() {
        let at_end = i == tv.len();
        let changed = !at_end && tv[i].1 != tv[run_start_idx].1;

        if changed || at_end {
            let run_len = i - run_start_idx;
            let (t0, v0) = &tv[run_start_idx];

            if run_len > STABLE_RUN_THRESHOLD {
                let t_end = tv[i - 1].0;
                result.push(json!({
                    "time":         t0,
                    "time_end":     t_end,
                    "value":        v0,
                    "stable":       true,
                    "sample_count": run_len,
                }));
            } else {
                for j in run_start_idx..i {
                    let (t, v) = &tv[j];
                    result.push(json!({"time": t, "value": v}));
                }
            }
            run_start_idx = i;
            if result.len() >= MAX_EVENTS {
                break;
            }
        }
        i += 1;
    }

    result
}

/// Compute simple statistics over a time-value series.
pub struct SignalStats {
    pub transition_count: usize,
    pub first_time_ns: f64,
    pub last_time_ns: f64,
    pub value_counts: HashMap<String, usize>,
}

pub fn compute_stats(tv: &[(f64, String)]) -> SignalStats {
    let mut value_counts: HashMap<String, usize> = HashMap::new();
    for (_, v) in tv {
        *value_counts.entry(v.clone()).or_insert(0) += 1;
    }
    SignalStats {
        transition_count: tv.len(),
        first_time_ns: tv.first().map(|(t, _)| *t).unwrap_or(0.0),
        last_time_ns: tv.last().map(|(t, _)| *t).unwrap_or(0.0),
        value_counts,
    }
}

// ── L2: semantic extraction ─────────────────────────────────────────────────

fn l2_clock_summary(sig_name: &str, events: &[Value]) -> String {
    if events.len() < 4 {
        return format!("{}: insufficient data for clock analysis", sig_name);
    }
    let rising: Vec<f64> = events
        .iter()
        .filter(|e| e.get("value").and_then(|v| v.as_str()) == Some("1"))
        .map(|e| e.get("time").and_then(|v| v.as_f64()).unwrap_or(0.0))
        .collect();
    if rising.len() < 2 {
        return format!("{}: no rising edges detected", sig_name);
    }
    let n = rising.len().min(11) - 1;
    let mean_period: f64 = (0..n).map(|i| rising[i + 1] - rising[i]).sum::<f64>() / n as f64;
    let freq_mhz = if mean_period > 0.0 { 1000.0 / mean_period } else { 0.0 };
    let first_t = events.first().and_then(|e| e.get("time")).and_then(|v| v.as_f64()).unwrap_or(0.0);
    let last_t = events.last().and_then(|e| e.get("time")).and_then(|v| v.as_f64()).unwrap_or(0.0);
    format!(
        "{}: {:.2} MHz (period {:.2} ns), {} rising edges in {:.0} ns",
        sig_name,
        freq_mhz,
        mean_period,
        rising.len(),
        last_t - first_t
    )
}

fn l2_bus_summary(sig_name: &str, events: &[Value]) -> String {
    if events.is_empty() {
        return format!("{}: no data", sig_name);
    }
    let changes: Vec<(f64, &str)> = events
        .iter()
        .filter_map(|e| {
            let v = e.get("value").and_then(|x| x.as_str())?;
            if v.contains('x') || v.contains('z') {
                return None;
            }
            let t = e.get("time").and_then(|x| x.as_f64())?;
            Some((t, v))
        })
        .collect();
    if changes.is_empty() {
        return format!("{}: all X/Z", sig_name);
    }
    let mut unique: Vec<&str> = Vec::new();
    for (_, v) in &changes {
        if !unique.iter().any(|u| u == v) {
            unique.push(v);
        }
    }
    let preview: Vec<&&str> = unique.iter().take(5).collect();
    let suffix = if unique.len() > 5 {
        format!("... ({} unique values)", unique.len())
    } else {
        String::new()
    };
    let span = changes.last().unwrap().0 - changes.first().unwrap().0;
    let preview_str: Vec<&str> = preview.into_iter().copied().collect();
    format!(
        "{}: {}{} over {:.0} ns",
        sig_name,
        preview_str.join(", "),
        suffix,
        span
    )
}

// ── L3: query-aware pruning ─────────────────────────────────────────────────

pub fn l3_prune(
    compressed: HashMap<String, Vec<Value>>,
    query_keywords: &[String],
    token_budget: usize,
    chars_per_token: usize,
) -> HashMap<String, Vec<Value>> {
    let char_budget = token_budget * chars_per_token;
    let mut scored: Vec<(usize, String, Vec<Value>)> = compressed
        .into_iter()
        .map(|(name, events)| {
            let name_lc = name.to_lowercase();
            let score = query_keywords
                .iter()
                .filter(|kw| name_lc.contains(&kw.to_lowercase()))
                .count();
            (score, name, events)
        })
        .collect();
    scored.sort_by(|a, b| b.0.cmp(&a.0));

    let mut result: HashMap<String, Vec<Value>> = HashMap::new();
    let mut used_chars: usize = 0;
    for (_, name, events) in scored {
        let approx_len: usize = events
            .iter()
            .map(|e| e.to_string().len())
            .sum::<usize>()
            + 4;
        if used_chars + approx_len > char_budget {
            let remaining = char_budget.saturating_sub(used_chars);
            let max_events = (remaining / 40).max(1);
            let trimmed: Vec<Value> = events.into_iter().take(max_events).collect();
            result.insert(name, trimmed);
            break;
        }
        used_chars += approx_len;
        result.insert(name, events);
    }
    result
}

/// L1 over all signals; cap each signal to `max_events_per_signal`.
pub fn l1_compress_all(meta: &WaveformMeta, max_events_per_signal: usize) -> HashMap<String, Vec<Value>> {
    let mut out: HashMap<String, Vec<Value>> = HashMap::new();
    for (name, sig) in &meta.signals {
        let mut compressed = l1_sample(&sig.tv);
        if compressed.len() > max_events_per_signal {
            // first + evenly-spaced middle + last
            let step = compressed.len() / max_events_per_signal;
            let mut shrunk: Vec<Value> = Vec::new();
            shrunk.push(compressed.first().cloned().unwrap());
            let mut idx = 1usize;
            while idx + step < compressed.len() {
                shrunk.push(compressed[idx].clone());
                idx += step;
            }
            shrunk.push(compressed.last().cloned().unwrap());
            compressed = shrunk;
        }
        out.insert(name.clone(), compressed);
    }
    out
}

pub struct CompressedForLlm {
    pub metadata_summary: String,
    pub clock_summaries: Vec<String>,
    pub event_narrative: String,
    pub signal_events: Map<String, Value>,
}

pub fn compress_for_llm(meta: &WaveformMeta, query: &str, token_budget: usize) -> CompressedForLlm {
    let compressed = l1_compress_all(meta, 200);

    // L2 clock summaries.
    let mut clock_summaries: Vec<String> = Vec::new();
    for (name, sig) in &meta.signals {
        if sig.is_clock() {
            if let Some(events) = compressed.get(name) {
                clock_summaries.push(l2_clock_summary(name, events));
            }
        }
    }

    // L3 pruning on non-clock signals.
    let keywords: Vec<String> = query
        .to_lowercase()
        .split(|c: char| !c.is_alphanumeric() && c != '_')
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .collect();

    let non_clock: HashMap<String, Vec<Value>> = compressed
        .iter()
        .filter(|(n, _)| meta.signals.get(*n).map(|s| !s.is_clock()).unwrap_or(true))
        .map(|(n, v)| (n.clone(), v.clone()))
        .collect();
    let budget = token_budget.saturating_sub(400);
    let pruned = l3_prune(non_clock, &keywords, budget, 4);

    // Event narrative.
    let mut narrative: Vec<String> = Vec::new();
    for (name, events) in &pruned {
        if events.is_empty() {
            continue;
        }
        let width = meta.signals.get(name).map(|s| s.width).unwrap_or(1);
        if width == 1 {
            let transitions: Vec<String> = events
                .iter()
                .take(20)
                .map(|e| {
                    let t = e.get("time").and_then(|v| v.as_f64()).unwrap_or(0.0);
                    let v = e.get("value").and_then(|v| v.as_str()).unwrap_or("?");
                    let symbol = match v {
                        "1" => "↑",
                        "0" => "↓",
                        other => other,
                    };
                    format!("t={:.1}ns: {}", t, symbol)
                })
                .collect();
            narrative.push(format!("{}: {}", name, transitions.join(", ")));
        } else {
            narrative.push(l2_bus_summary(name, events));
        }
    }

    let duration = meta.duration_ns;
    let clocks = meta.signals.values().filter(|s| s.is_clock()).count();
    let metadata_summary = format!(
        "Duration: {:.0} ns | Signals: {} | Clocks: {}",
        duration,
        meta.signals.len(),
        clocks
    );

    // Convert HashMap → serde Map for downstream JSON shape.
    let mut signal_events: Map<String, Value> = Map::new();
    for (name, events) in compressed {
        signal_events.insert(name, Value::Array(events));
    }

    CompressedForLlm {
        metadata_summary,
        clock_summaries,
        event_narrative: narrative.join("\n"),
        signal_events,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::{SignalTrace, WaveformMeta};

    fn make_meta_with_clock() -> WaveformMeta {
        let mut signals = std::collections::HashMap::new();
        let tv: Vec<(f64, String)> = (0..40)
            .map(|i| (i as f64 * 5.0, if i % 2 == 0 { "0" } else { "1" }.to_string()))
            .collect();
        signals.insert(
            "tb.clk".to_string(),
            SignalTrace {
                name: "tb.clk".into(),
                width: 1,
                scope: "tb".into(),
                tv,
            },
        );
        WaveformMeta {
            format: "vcd".into(),
            file_path: "test.vcd".into(),
            duration_ns: 200.0,
            signals,
        }
    }

    #[test]
    fn l1_sample_preserves_short_traces() {
        let tv = vec![(0.0, "0".into()), (5.0, "1".into()), (10.0, "0".into())];
        let out = l1_sample(&tv);
        assert_eq!(out.len(), 3);
    }

    #[test]
    fn l2_clock_summary_reports_mhz() {
        let events: Vec<Value> = (0..20)
            .map(|i| json!({"time": i as f64 * 5.0, "value": if i % 2 == 0 {"0"} else {"1"}}))
            .collect();
        let summary = l2_clock_summary("clk", &events);
        assert!(summary.contains("MHz"), "missing MHz: {}", summary);
    }

    #[test]
    fn l3_prune_keeps_query_match_first() {
        let mut compressed: HashMap<String, Vec<Value>> = HashMap::new();
        compressed.insert("unrelated_signal".into(), vec![json!({"time": 0, "value": "0"})]);
        compressed.insert("count_reg".into(), vec![json!({"time": 0, "value": "1"})]);

        let kws = vec!["count".to_string()];
        let pruned = l3_prune(compressed, &kws, 100, 4);
        assert!(pruned.contains_key("count_reg"));
    }

    #[test]
    fn compress_for_llm_includes_clock_summary() {
        let meta = make_meta_with_clock();
        let out = compress_for_llm(&meta, "", 4000);
        assert!(!out.clock_summaries.is_empty());
        assert!(out.metadata_summary.contains("Clocks"));
    }
}
