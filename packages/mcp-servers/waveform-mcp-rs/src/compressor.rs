//! L1 activity-aware waveform compression.
//!
//! Preserves signal edges (value transitions) and compresses stable regions
//! to fit within LLM token budgets.

/// Maximum events returned per signal before sampling kicks in.
const MAX_EVENTS: usize = 500;

/// Stable-run threshold: compress consecutive identical values longer than this.
const STABLE_RUN_THRESHOLD: usize = 8;

/// L1 compression: preserve all transitions, collapse stable runs.
///
/// Returns a JSON array of `{"time": f64, "value": str}` objects.
/// Stable runs are collapsed into `{"time_start": f64, "time_end": f64, "value": str, "stable": true}`.
pub fn l1_sample(tv: &[(f64, String)]) -> Vec<serde_json::Value> {
    if tv.is_empty() {
        return Vec::new();
    }
    if tv.len() <= MAX_EVENTS {
        return tv
            .iter()
            .map(|(t, v)| serde_json::json!({"time": t, "value": v}))
            .collect();
    }

    let mut result: Vec<serde_json::Value> = Vec::new();
    let mut run_start_idx: usize = 0;

    let mut i = 1usize;
    while i <= tv.len() {
        let at_end = i == tv.len();
        let changed = !at_end && tv[i].1 != tv[run_start_idx].1;

        if changed || at_end {
            let run_len = i - run_start_idx;
            let (t0, v0) = &tv[run_start_idx];

            if run_len > STABLE_RUN_THRESHOLD {
                // Emit a stable-region entry
                let t_end = if at_end {
                    tv[i - 1].0
                } else {
                    tv[i - 1].0
                };
                result.push(serde_json::json!({
                    "time":       t0,
                    "time_end":   t_end,
                    "value":      v0,
                    "stable":     true,
                    "sample_count": run_len,
                }));
            } else {
                // Emit each event individually
                for j in run_start_idx..i {
                    let (t, v) = &tv[j];
                    result.push(serde_json::json!({"time": t, "value": v}));
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
    pub value_counts: std::collections::HashMap<String, usize>,
}

pub fn compute_stats(tv: &[(f64, String)]) -> SignalStats {
    let mut value_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
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
