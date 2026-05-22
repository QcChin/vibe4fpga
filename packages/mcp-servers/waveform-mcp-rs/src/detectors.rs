//! Five waveform anomaly detectors.
//!
//! Ports `detectors.py` from the deleted Python `waveform-mcp` package. Each
//! detector returns a `Vec<Anomaly>`. `run_all` executes them concurrently on
//! a tokio runtime via `spawn_blocking` (each detector is pure CPU).
//!
//! Carries the bug fixes we made on the Python side last round:
//!   * Glitch detector skips clock signals (bug #12).
//!   * CDC detector deduplicates clock aliases by edge fingerprint (bug #13).

use serde::Serialize;
use serde_json::{json, Map, Value};

#[derive(Clone, Debug, Serialize)]
pub struct Anomaly {
    pub detector: String,
    pub severity: String,        // "error" | "warning" | "info"
    pub signal:   String,
    pub time_ns:  f64,
    pub anomaly_type: String,
    pub message:  String,
    pub rtl_hint: String,
}

impl Anomaly {
    pub fn to_json_with_index(&self, index: usize) -> Value {
        json!({
            "index":    index,
            "detector": self.detector,
            "severity": self.severity,
            "signal":   self.signal,
            "time_ns":  self.time_ns,
            "type":     self.anomaly_type,
            "message":  self.message,
            "rtl_hint": self.rtl_hint,
        })
    }
}

/// An event payload as produced by the compressor: `{time: f64, value: str, [stable: bool]}`.
/// Helper accessors for the detector code.
fn event_time(ev: &Value) -> f64 {
    ev.get("time").and_then(|v| v.as_f64()).unwrap_or(0.0)
}

fn event_value(ev: &Value) -> &str {
    ev.get("value").and_then(|v| v.as_str()).unwrap_or("")
}

// ── 1. Glitch Detector ───────────────────────────────────────────────────────

pub fn detect_glitches(
    signal_events: &Map<String, Value>,
    clock_period_ns: f64,
    clock_names: &[String],
) -> Vec<Anomaly> {
    let mut anomalies = Vec::new();
    if clock_period_ns <= 0.0 {
        return anomalies;
    }

    let skip: std::collections::HashSet<&String> = clock_names.iter().collect();

    for (name, events_val) in signal_events {
        if skip.contains(name) {
            continue;
        }
        let events = match events_val.as_array() {
            Some(arr) if arr.len() >= 3 => arr,
            _ => continue,
        };

        for i in 1..events.len() - 1 {
            let v0 = event_value(&events[i - 1]);
            let v1 = event_value(&events[i]);
            let v2 = event_value(&events[i + 1]);
            if v1 != v0 && v1 != v2 && v0 == v2 {
                let t1 = event_time(&events[i]);
                let t2 = event_time(&events[i + 1]);
                let pulse_width = t2 - t1;
                if pulse_width > 0.0 && pulse_width < clock_period_ns {
                    anomalies.push(Anomaly {
                        detector: "GlitchDetector".into(),
                        severity: "warning".into(),
                        signal: name.clone(),
                        time_ns: t1,
                        anomaly_type: "glitch".into(),
                        message: format!(
                            "Glitch on {}: pulse width {:.2} ns < 1 clock period ({:.2} ns). \
                             Possible combinational race hazard.",
                            name, pulse_width, clock_period_ns
                        ),
                        rtl_hint: format!("Check combinational logic driving {}", name),
                    });
                }
            }
        }
    }
    anomalies
}

// ── 2. X/Z State Tracker ─────────────────────────────────────────────────────

pub fn track_xz_states(signal_events: &Map<String, Value>) -> Vec<Anomaly> {
    let mut anomalies = Vec::new();
    for (name, events_val) in signal_events {
        let events = match events_val.as_array() {
            Some(a) => a,
            None => continue,
        };
        let mut in_xz = false;
        for evt in events {
            let val = event_value(evt).to_ascii_lowercase();
            let is_xz = val.contains('x') || val.contains('z');
            if is_xz && !in_xz {
                in_xz = true;
                let kind = if val.contains('x') { 'X' } else { 'Z' };
                let t = event_time(evt);
                anomalies.push(Anomaly {
                    detector: "XZStateTracker".into(),
                    severity: "error".into(),
                    signal: name.clone(),
                    time_ns: t,
                    anomaly_type: if kind == 'X' { "x_state" } else { "z_state" }.into(),
                    message: format!(
                        "{} entered {} state at {:.1} ns. Possible uninitialized register or \
                         missing reset.",
                        name, kind, t
                    ),
                    rtl_hint: format!("Check reset coverage for {}; verify no undriven nets", name),
                });
            } else if !is_xz {
                in_xz = false;
            }
        }
    }
    anomalies
}

// ── 3. CDC Detector ──────────────────────────────────────────────────────────

pub fn detect_cdc(
    signal_events: &Map<String, Value>,
    clock_names: &[String],
    min_sync_stages: usize,
) -> Vec<Anomaly> {
    let mut anomalies = Vec::new();
    if clock_names.len() < 2 {
        return anomalies;
    }

    // Collect rising edges for each clock.
    let mut clock_edges: Vec<(String, Vec<f64>)> = Vec::new();
    for clk in clock_names {
        let events = signal_events
            .get(clk)
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let mut edges = Vec::new();
        for i in 1..events.len() {
            let prev = event_value(&events[i - 1]);
            let cur = event_value(&events[i]);
            if (prev == "0" || prev == "x") && cur == "1" {
                edges.push(event_time(&events[i]));
            }
        }
        clock_edges.push((clk.clone(), edges));
    }

    // Dedup clock aliases by fingerprint of the first 6 edges (bug #13).
    let mut unique: Vec<(String, Vec<f64>)> = Vec::new();
    for (name, edges) in clock_edges.into_iter() {
        if edges.is_empty() {
            continue;
        }
        let fp: &[f64] = &edges[..edges.len().min(6)];
        let already = unique.iter().any(|(_, e)| {
            let other_fp: &[f64] = &e[..e.len().min(6)];
            fp.len() == other_fp.len()
                && fp
                    .iter()
                    .zip(other_fp.iter())
                    .all(|(a, b)| (a - b).abs() < 0.001)
        });
        if !already {
            unique.push((name, edges));
        }
    }
    if unique.len() < 2 {
        return anomalies;
    }

    // Estimate per-clock period.
    let periods: Vec<f64> = unique
        .iter()
        .map(|(_, edges)| if edges.len() >= 2 { edges[1] - edges[0] } else { 10.0 })
        .collect();

    let clock_name_set: std::collections::HashSet<&String> =
        clock_names.iter().collect();

    for (name, events_val) in signal_events {
        if clock_name_set.contains(name) {
            continue;
        }
        let events = match events_val.as_array() {
            Some(a) => a,
            None => continue,
        };

        let mut transitions = Vec::new();
        for i in 1..events.len() {
            if event_value(&events[i]) != event_value(&events[i - 1]) {
                transitions.push(event_time(&events[i]));
            }
        }

        let mut counts = vec![0usize; unique.len()];
        for &t in &transitions {
            for (idx, ((_clk, edges), &period)) in unique.iter().zip(periods.iter()).enumerate() {
                let tol = period * 0.1;
                if edges.iter().any(|&e| (t - e).abs() < tol) {
                    counts[idx] += 1;
                }
            }
        }

        let active: Vec<&str> = unique
            .iter()
            .zip(counts.iter())
            .filter_map(|((clk, _), &c)| if c > 0 { Some(clk.as_str()) } else { None })
            .collect();

        if active.len() >= 2 {
            anomalies.push(Anomaly {
                detector: "CDCDetector".into(),
                severity: "warning".into(),
                signal: name.clone(),
                time_ns: *transitions.first().unwrap_or(&0.0),
                anomaly_type: "cdc_suspect".into(),
                message: format!(
                    "{} transitions correlate with {} clock domains ({}). Possible CDC \
                     crossing — verify {}-FF synchronizer is present.",
                    name,
                    active.len(),
                    active.join(", "),
                    min_sync_stages
                ),
                rtl_hint: format!("Search RTL for {} to verify 2-FF synchronizer", name),
            });
        }
    }

    anomalies
}

// ── 4. AXI Protocol Violations (wraps axi_decoder) ───────────────────────────

pub fn detect_axi_violations(
    signal_events: &Map<String, Value>,
    axi_prefix: &str,
    clock_name: Option<&str>,
) -> Vec<Anomaly> {
    if axi_prefix.is_empty() {
        return Vec::new();
    }
    let result = crate::axi_decoder::decode_axi(signal_events, axi_prefix, clock_name, 100);
    let mut anomalies = Vec::new();
    if let Some(violations) = result.get("violations").and_then(|v| v.as_array()) {
        for v in violations {
            let channel = v.get("channel").and_then(|x| x.as_str()).unwrap_or("?");
            anomalies.push(Anomaly {
                detector: "AXIProtocolDecoder".into(),
                severity: "error".into(),
                signal: format!("{}{}valid", axi_prefix, channel.to_lowercase()),
                time_ns: v.get("time_ns").and_then(|x| x.as_f64()).unwrap_or(0.0),
                anomaly_type: v
                    .get("violation_type")
                    .and_then(|x| x.as_str())
                    .unwrap_or("axi_violation")
                    .to_string(),
                message: v
                    .get("message")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string(),
                rtl_hint: v
                    .get("suggestion")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }
    }
    anomalies
}

// ── 5. Handshake Timeout Detector ────────────────────────────────────────────

pub fn detect_handshake_timeouts(
    signal_events: &Map<String, Value>,
    clock_period_ns: f64,
    timeout_cycles: u32,
) -> Vec<Anomaly> {
    let patterns = ["_valid", "_req", "_en", "valid", "req"];
    let timeout_ns = timeout_cycles as f64 * clock_period_ns;
    let mut anomalies = Vec::new();

    for (name, events_val) in signal_events {
        if !patterns.iter().any(|p| name.contains(p)) {
            continue;
        }
        let events = match events_val.as_array() {
            Some(a) => a,
            None => continue,
        };
        let mut assert_start: Option<f64> = None;
        for evt in events {
            let val = event_value(evt);
            let t = event_time(evt);
            if val == "1" {
                if assert_start.is_none() {
                    assert_start = Some(t);
                } else if let Some(start) = assert_start {
                    if t - start > timeout_ns {
                        anomalies.push(Anomaly {
                            detector: "HandshakeTimeout".into(),
                            severity: "warning".into(),
                            signal: name.clone(),
                            time_ns: start,
                            anomaly_type: "handshake_timeout".into(),
                            message: format!(
                                "{} asserted for >{} clocks ({:.0} ns) without deassertion. \
                                 Check if partner ready/ack signal responds.",
                                name,
                                timeout_cycles,
                                t - start
                            ),
                            rtl_hint: format!("Inspect downstream block connected to {}", name),
                        });
                        assert_start = None;
                    }
                }
            } else {
                assert_start = None;
            }
        }
    }
    anomalies
}

// ── Run all detectors in parallel ────────────────────────────────────────────

pub async fn run_all_detectors(
    signal_events: Map<String, Value>,
    clock_period_ns: f64,
    clock_names: Vec<String>,
    axi_prefix: String,
    timeout_cycles: u32,
) -> Vec<Anomaly> {
    let events = std::sync::Arc::new(signal_events);
    let clocks = std::sync::Arc::new(clock_names);
    let prefix = std::sync::Arc::new(axi_prefix);

    let e1 = events.clone();
    let c1 = clocks.clone();
    let t1 = tokio::task::spawn_blocking(move || detect_glitches(&e1, clock_period_ns, &c1));

    let e2 = events.clone();
    let t2 = tokio::task::spawn_blocking(move || track_xz_states(&e2));

    let e3 = events.clone();
    let c3 = clocks.clone();
    let t3 = tokio::task::spawn_blocking(move || detect_cdc(&e3, &c3, 2));

    let e4 = events.clone();
    let p4 = prefix.clone();
    let t4 = tokio::task::spawn_blocking(move || detect_axi_violations(&e4, &p4, None));

    let e5 = events.clone();
    let t5 = tokio::task::spawn_blocking(move || {
        detect_handshake_timeouts(&e5, clock_period_ns, timeout_cycles)
    });

    let mut anomalies: Vec<Anomaly> = Vec::new();
    for join in [t1, t2, t3, t4, t5] {
        if let Ok(part) = join.await {
            anomalies.extend(part);
        }
    }

    // Sort by severity (error first) then by time.
    let severity_rank = |s: &str| match s {
        "error" => 0,
        "warning" => 1,
        "info" => 2,
        _ => 3,
    };
    anomalies.sort_by(|a, b| {
        severity_rank(&a.severity)
            .cmp(&severity_rank(&b.severity))
            .then(a.time_ns.partial_cmp(&b.time_ns).unwrap_or(std::cmp::Ordering::Equal))
    });
    anomalies
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn evt(t: f64, v: &str) -> Value {
        json!({"time": t, "value": v})
    }

    fn make_events(name: &str, points: Vec<(f64, &str)>) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert(
            name.to_string(),
            Value::Array(points.into_iter().map(|(t, v)| evt(t, v)).collect()),
        );
        m
    }

    #[test]
    fn glitch_detector_skips_clock_signals() {
        // Clock with 5ns/5ns toggle would otherwise be flagged on every cycle.
        let mut m = Map::new();
        m.insert(
            "clk".to_string(),
            Value::Array(vec![
                evt(0.0, "0"),
                evt(5.0, "1"),
                evt(10.0, "0"),
                evt(15.0, "1"),
                evt(20.0, "0"),
            ]),
        );
        let anomalies = detect_glitches(&m, 10.0, &["clk".to_string()]);
        assert!(anomalies.is_empty(), "clock was misreported as glitch: {:?}", anomalies);
    }

    #[test]
    fn glitch_detector_flags_real_glitch() {
        let m = make_events(
            "data",
            vec![
                (0.0, "0"),
                (10.0, "1"),
                (12.0, "0"),  // 2ns pulse @ 10ns clock period → glitch
                (100.0, "0"),
            ],
        );
        let anomalies = detect_glitches(&m, 10.0, &[]);
        assert_eq!(anomalies.len(), 1);
        assert_eq!(anomalies[0].anomaly_type, "glitch");
    }

    #[test]
    fn xz_tracker_flags_x_entry() {
        let m = make_events("d", vec![(0.0, "0"), (5.0, "x"), (10.0, "1")]);
        let anomalies = track_xz_states(&m);
        assert_eq!(anomalies.len(), 1);
        assert_eq!(anomalies[0].anomaly_type, "x_state");
        assert_eq!(anomalies[0].severity, "error");
    }

    #[test]
    fn cdc_detector_dedups_aliased_clocks() {
        // Two clock names with identical edge timestamps — same physical net.
        let mut m = Map::new();
        let edges = vec![
            evt(0.0, "0"),
            evt(5.0, "1"),
            evt(10.0, "0"),
            evt(15.0, "1"),
            evt(20.0, "0"),
        ];
        m.insert("tb.clk".into(), Value::Array(edges.clone()));
        m.insert("tb.dut.clk".into(), Value::Array(edges));
        m.insert(
            "data".into(),
            Value::Array(vec![evt(0.0, "0"), evt(15.0, "1")]),
        );
        let anomalies = detect_cdc(
            &m,
            &["tb.clk".into(), "tb.dut.clk".into()],
            2,
        );
        assert!(anomalies.is_empty(), "alias dedup failed: {:?}", anomalies);
    }

    #[test]
    fn handshake_timeout_fires_on_long_assert() {
        let m = make_events(
            "req_valid",
            vec![(0.0, "0"), (10.0, "1"), (5000.0, "1"), (5010.0, "0")],
        );
        let anomalies = detect_handshake_timeouts(&m, 10.0, 100);
        assert_eq!(anomalies.len(), 1);
        assert_eq!(anomalies[0].anomaly_type, "handshake_timeout");
    }
}
