//! debug_waveform skill orchestrator.
//!
//! Pipeline (mirrors the Python `skill.py::run`):
//!   1. Compress waveform via L1 + L3 to get signal_events + narrative.
//!   2. Detect clock period + clock signal names from signal_events.
//!   3. Run 5 detectors concurrently.
//!   4. Build a token-budgeted LLM context bundle.
//!   5. Call Anthropic (non-stream) and parse the JSON response.
//!
//! When the LLM call cannot be made (missing key, network error), the
//! deterministic anomaly list still ships — only `llm_analysis` is empty and
//! `summary` reports the skipped step. This matches the user-visible contract
//! of the Python version when ANTHROPIC_* env vars are not set.

use crate::compressor::compress_for_llm;
use crate::detectors::{run_all_detectors, Anomaly};
use crate::llm::{parse_json_response, AnthropicClient};
use crate::parser::WaveformMeta;
use serde_json::{json, Map, Value};

const SYSTEM_PROMPT: &str = "You are an FPGA debug engineer analyzing simulation waveform anomalies.\n\
\n\
Given:\n  \
  1. Simulation metadata (duration, clocks, signal count)\n  \
  2. Detected anomalies (from automated detectors)\n  \
  3. Compressed waveform events (edge narrative)\n\
\n\
For each anomaly, provide:\n  \
  - Root cause explanation (based on FPGA/digital design principles)\n  \
  - Specific RTL fix recommendation\n  \
  - Whether the issue is critical (blocks functionality) or advisory\n\
\n\
Output as JSON array:\n\
[\n  \
  {\n    \
    \"anomaly_index\": int,\n    \
    \"root_cause\":    \"string — technical explanation\",\n    \
    \"fix\":           \"string — specific RTL change to make\",\n    \
    \"severity\":      \"critical|major|minor\",\n    \
    \"confidence\":    \"high|medium|low\"\n  \
  }\n\
]";

const CHARS_PER_TOKEN: usize = 4;
const METADATA_TOKENS: usize = 150;

pub struct DebugInput {
    pub query: String,
    pub axi_prefix: String,
    pub token_budget: usize,
    pub signal_whitelist: Option<Vec<String>>,
    pub max_tokens: u32,
    pub temperature: f64,
}

impl Default for DebugInput {
    fn default() -> Self {
        DebugInput {
            query: String::new(),
            axi_prefix: String::new(),
            token_budget: 4000,
            signal_whitelist: None,
            max_tokens: 16384,
            temperature: 0.2,
        }
    }
}

/// Infer clock period (ns) + clock signal names from the compressed events.
fn detect_clock(signal_events: &Map<String, Value>) -> (f64, Vec<String>) {
    let mut clock_period_ns = 10.0;
    let mut clock_names: Vec<String> = Vec::new();
    for (name, events_val) in signal_events {
        if !name.to_lowercase().contains("clk") {
            continue;
        }
        let events = match events_val.as_array() {
            Some(a) if a.len() > 10 => a,
            _ => continue,
        };
        let mut rising: Vec<f64> = Vec::new();
        for i in 1..events.len() {
            let prev = events[i - 1].get("value").and_then(|v| v.as_str()).unwrap_or("");
            let cur = events[i].get("value").and_then(|v| v.as_str()).unwrap_or("");
            if (prev == "0" || prev == "x") && cur == "1" {
                rising.push(events[i].get("time").and_then(|v| v.as_f64()).unwrap_or(0.0));
            }
        }
        if rising.len() >= 2 {
            let span = rising.last().unwrap() - rising.first().unwrap();
            clock_period_ns = span / (rising.len() - 1) as f64;
            clock_names.push(name.clone());
        }
    }
    (clock_period_ns, clock_names)
}

fn build_llm_context(
    metadata_summary: &str,
    event_narrative: &str,
    anomalies: &[Anomaly],
    token_budget: usize,
) -> (String, Vec<Value>) {
    let char_budget = token_budget * CHARS_PER_TOKEN;
    let mut parts: Vec<String> = vec![format!("## Simulation Metadata\n{}", metadata_summary)];
    let mut used = METADATA_TOKENS * CHARS_PER_TOKEN;

    let mut anomaly_dicts: Vec<Value> = Vec::new();
    for (i, a) in anomalies.iter().enumerate() {
        let chunk = format!(
            "\n### Anomaly {}: [{}] {}\nSignal: {} @ t={:.1} ns\nDetector: {}\nMessage: {}\nRTL hint: {}\n",
            i,
            a.severity.to_uppercase(),
            a.anomaly_type,
            a.signal,
            a.time_ns,
            a.detector,
            a.message,
            a.rtl_hint,
        );
        if used + chunk.len() > char_budget {
            break;
        }
        used += chunk.len();
        parts.push(chunk);
        anomaly_dicts.push(a.to_json_with_index(i));
    }

    if char_budget > used + 200 && !event_narrative.is_empty() {
        let remaining = char_budget - used - 100;
        let cap = remaining.min(event_narrative.len());
        parts.push(format!(
            "\n## Waveform Events (compressed)\n{}",
            &event_narrative[..cap]
        ));
    }

    (parts.join("\n"), anomaly_dicts)
}

pub async fn run(meta: &WaveformMeta, input: DebugInput) -> Value {
    // Step 1 — optional whitelist on signal set BEFORE compression.
    let meta_for_compress: WaveformMeta = if let Some(whitelist) = input.signal_whitelist.as_ref() {
        let allow: std::collections::HashSet<&String> = whitelist.iter().collect();
        let mut filtered = std::collections::HashMap::new();
        for (n, s) in &meta.signals {
            if allow.contains(n) || s.is_clock() {
                filtered.insert(n.clone(), s.clone());
            }
        }
        WaveformMeta {
            format: meta.format.clone(),
            file_path: meta.file_path.clone(),
            duration_ns: meta.duration_ns,
            signals: if filtered.is_empty() {
                meta.signals.clone()
            } else {
                filtered
            },
        }
    } else {
        meta.clone()
    };

    let compressed = compress_for_llm(&meta_for_compress, &input.query, input.token_budget);

    // Step 2 — clock detection.
    let (clock_period_ns, clock_names) = detect_clock(&compressed.signal_events);

    // Step 3 — 5 detectors concurrently.
    let anomalies = run_all_detectors(
        compressed.signal_events.clone(),
        clock_period_ns,
        clock_names,
        input.axi_prefix.clone(),
        100,
    )
    .await;

    let (context, anomaly_dicts) = build_llm_context(
        &compressed.metadata_summary,
        &compressed.event_narrative,
        &anomalies,
        input.token_budget,
    );

    // Step 4 — LLM call (only when anomalies exist).
    let mut llm_analysis: Value = json!([]);
    let mut llm_status: Option<String> = None;
    if !anomalies.is_empty() {
        match AnthropicClient::from_env() {
            Ok(client) => {
                let user_prompt = format!(
                    "{}\n\nEngineer's question: {}",
                    context,
                    if input.query.is_empty() {
                        "Analyze all anomalies."
                    } else {
                        &input.query
                    }
                );
                match client
                    .complete(SYSTEM_PROMPT, &user_prompt, input.temperature, input.max_tokens)
                    .await
                {
                    Ok(raw) => match parse_json_response(&raw) {
                        Ok(parsed) => llm_analysis = parsed,
                        Err(_) => {
                            llm_analysis = json!([{"raw_response": raw}]);
                        }
                    },
                    Err(e) => {
                        llm_status = Some(format!("LLM call failed: {}", e));
                    }
                }
            }
            Err(e) => {
                llm_status = Some(format!("LLM step skipped: {}", e));
            }
        }
    }

    let error_count = anomalies.iter().filter(|a| a.severity == "error").count();
    let warning_count = anomalies.iter().filter(|a| a.severity == "warning").count();

    let mut summary = format!(
        "WaveformDebug: {} error(s), {} warning(s) detected",
        error_count, warning_count
    );
    if let Some(extra) = llm_status {
        summary.push_str(" — ");
        summary.push_str(&extra);
    }

    json!({
        "anomaly_count":  anomalies.len(),
        "error_count":    error_count,
        "warning_count":  warning_count,
        "anomalies":      anomaly_dicts,
        "llm_analysis":   llm_analysis,
        "summary":        summary,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::{SignalTrace, WaveformMeta};

    fn meta_with_glitch() -> WaveformMeta {
        let mut signals = std::collections::HashMap::new();
        // clock — clean 100 MHz
        let clk: Vec<(f64, String)> = (0..40)
            .map(|i| (i as f64 * 5.0, if i % 2 == 0 { "0" } else { "1" }.into()))
            .collect();
        signals.insert(
            "tb.clk".to_string(),
            SignalTrace {
                name: "tb.clk".into(),
                width: 1,
                scope: "tb".into(),
                tv: clk,
            },
        );
        // glitching data signal
        signals.insert(
            "tb.data".to_string(),
            SignalTrace {
                name: "tb.data".into(),
                width: 1,
                scope: "tb".into(),
                tv: vec![
                    (0.0, "0".into()),
                    (10.0, "1".into()),
                    (12.0, "0".into()),
                    (100.0, "0".into()),
                ],
            },
        );
        WaveformMeta {
            format: "vcd".into(),
            file_path: "test.vcd".into(),
            duration_ns: 200.0,
            signals,
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn debug_returns_anomalies_without_llm_when_key_missing() {
        std::env::remove_var("ANTHROPIC_API_KEY");
        std::env::remove_var("ANTHROPIC_AUTH_TOKEN");
        let meta = meta_with_glitch();
        let result = run(&meta, DebugInput::default()).await;
        assert!(result["anomaly_count"].as_u64().unwrap_or(0) >= 1);
        // No key set → llm_analysis empty array, summary mentions skip.
        assert_eq!(result["llm_analysis"], json!([]));
        let s = result["summary"].as_str().unwrap();
        assert!(s.contains("skipped") || s.contains("failed"));
    }
}
