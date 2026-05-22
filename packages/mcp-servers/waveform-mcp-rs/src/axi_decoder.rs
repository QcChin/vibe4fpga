//! AXI4 5-channel state machine decoder.
//!
//! Port of `axi_decoder.py` from the deleted Python `waveform-mcp` package.
//! Reads compressed signal events for an AXI bus, samples valid/ready at each
//! rising clock edge, and emits both completed transactions and protocol
//! violations.
//!
//! Channels: AW (write addr), W (write data), B (write response),
//!           AR (read addr), R (read data).

use serde_json::{json, Map, Value};

const AXI_CHANNELS: &[(&str, &str, &str)] = &[
    ("aw", "awvalid", "awready"),
    ("w", "wvalid", "wready"),
    ("b", "bvalid", "bready"),
    ("ar", "arvalid", "arready"),
    ("r", "rvalid", "rready"),
];

fn event_time(ev: &Value) -> f64 {
    ev.get("time").and_then(|v| v.as_f64()).unwrap_or(0.0)
}

fn event_value(ev: &Value) -> &str {
    ev.get("value").and_then(|v| v.as_str()).unwrap_or("")
}

/// Last value at or before `t_ns`.
fn value_at(events: &[Value], t_ns: f64) -> Option<&str> {
    let mut val: Option<&str> = None;
    for ev in events {
        if event_time(ev) <= t_ns {
            val = Some(event_value(ev));
        } else {
            break;
        }
    }
    val
}

fn rising_edges(events: &[Value]) -> Vec<f64> {
    let mut edges = Vec::new();
    for i in 1..events.len() {
        let prev = event_value(&events[i - 1]);
        let cur = event_value(&events[i]);
        if (prev == "0" || prev == "x") && cur == "1" {
            edges.push(event_time(&events[i]));
        }
    }
    edges
}

pub fn decode_axi(
    signal_events: &Map<String, Value>,
    axi_prefix: &str,
    clock_name: Option<&str>,
    timeout_cycles: u32,
) -> Value {
    let sig = |suffix: &str| -> Vec<Value> {
        signal_events
            .get(&format!("{}{}", axi_prefix, suffix))
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default()
    };

    // Auto-detect clock if not provided.
    let detected_clock: Option<String> = clock_name.map(|s| s.to_string()).or_else(|| {
        signal_events.keys().find_map(|n| {
            let lc = n.to_lowercase();
            if lc.contains("clk") || lc.contains("clock") {
                Some(n.clone())
            } else {
                None
            }
        })
    });

    let clock_events = detected_clock
        .as_ref()
        .and_then(|c| signal_events.get(c))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let edges = rising_edges(&clock_events);
    if edges.is_empty() {
        return json!({
            "transactions": [],
            "violations":   [],
            "summary":      format!(
                "No clock edges found ({}). Cannot decode AXI transactions.",
                detected_clock.unwrap_or_else(|| "no clock detected".to_string())
            ),
        });
    }

    let mut transactions: Vec<Value> = Vec::new();
    let mut violations: Vec<Value> = Vec::new();
    let mut channel_handshakes: std::collections::HashMap<&str, Vec<f64>> =
        std::collections::HashMap::new();

    for (ch, valid_name, ready_name) in AXI_CHANNELS {
        let valid_tv = sig(valid_name);
        if valid_tv.is_empty() {
            continue;
        }
        let ready_tv = sig(ready_name);

        let mut handshakes: Vec<f64> = Vec::new();
        let mut valid_cycle_count: u32 = 0;

        for &edge in &edges {
            let valid_val = value_at(&valid_tv, edge).unwrap_or("0");
            let ready_val = if ready_tv.is_empty() {
                None
            } else {
                value_at(&ready_tv, edge)
            };

            if valid_val == "1" {
                valid_cycle_count += 1;
                if ready_val == Some("1") {
                    handshakes.push(edge);
                    valid_cycle_count = 0;
                } else if valid_cycle_count > timeout_cycles {
                    violations.push(json!({
                        "time_ns":         edge,
                        "channel":         ch.to_uppercase(),
                        "violation_type":  "handshake_timeout",
                        "message": format!(
                            "{}{} asserted for >{} cycles without {}{} response",
                            axi_prefix, valid_name, timeout_cycles, axi_prefix, ready_name
                        ),
                        "suggestion": "Check if slave is stalled or READY is driven correctly",
                    }));
                    valid_cycle_count = 0;
                }
            } else {
                valid_cycle_count = 0;
            }
        }

        channel_handshakes.insert(*ch, handshakes);
    }

    // Ordering violation: W before AW.
    let empty_vec: Vec<f64> = Vec::new();
    let aw_times = channel_handshakes.get("aw").unwrap_or(&empty_vec).clone();
    let w_times = channel_handshakes.get("w").unwrap_or(&empty_vec).clone();
    let b_times = channel_handshakes.get("b").unwrap_or(&empty_vec).clone();
    let ar_times = channel_handshakes.get("ar").unwrap_or(&empty_vec).clone();
    let r_times = channel_handshakes.get("r").unwrap_or(&empty_vec).clone();

    for &w_t in &w_times {
        if !aw_times.iter().any(|&aw| aw <= w_t) {
            violations.push(json!({
                "time_ns":         w_t,
                "channel":         "W",
                "violation_type":  "w_before_aw",
                "message": format!(
                    "Write data handshake at {:.1}ns before any write address handshake",
                    w_t
                ),
                "suggestion": "Ensure AWVALID/AWREADY completes before or concurrently with WVALID/WREADY",
            }));
        }
    }

    // Build write transactions (AW → matching W*, B?).
    let awaddr_tv = sig("awaddr");
    for (i, &aw_t) in aw_times.iter().enumerate() {
        let next_aw = aw_times.get(i + 1).copied().unwrap_or(f64::INFINITY);
        let matching_w: Vec<f64> = w_times.iter().copied().filter(|t| *t >= aw_t && *t < next_aw).collect();
        let b_t = b_times.iter().copied().find(|t| *t > aw_t);
        let addr = if awaddr_tv.is_empty() {
            Value::Null
        } else {
            value_at(&awaddr_tv, aw_t)
                .map(|s| Value::String(s.to_string()))
                .unwrap_or(Value::Null)
        };
        transactions.push(json!({
            "txn_id":     format!("write_{}", i),
            "type":       "write",
            "aw_time_ns": aw_t,
            "w_times_ns": matching_w,
            "b_time_ns":  b_t,
            "addr":       addr,
            "status":     if b_t.is_some() { "ok" } else { "no_response" },
        }));
    }

    // Build read transactions (AR → matching R*).
    let araddr_tv = sig("araddr");
    for (i, &ar_t) in ar_times.iter().enumerate() {
        let next_ar = ar_times.get(i + 1).copied().unwrap_or(f64::INFINITY);
        let matching_r: Vec<f64> = r_times.iter().copied().filter(|t| *t > ar_t && *t < next_ar).collect();
        let addr = if araddr_tv.is_empty() {
            Value::Null
        } else {
            value_at(&araddr_tv, ar_t)
                .map(|s| Value::String(s.to_string()))
                .unwrap_or(Value::Null)
        };
        transactions.push(json!({
            "txn_id":     format!("read_{}", i),
            "type":       "read",
            "ar_time_ns": ar_t,
            "r_times_ns": matching_r,
            "addr":       addr,
            "status":     if matching_r.is_empty() { "no_response" } else { "ok" },
        }));
    }

    let writes = transactions.iter().filter(|t| t["type"] == "write").count();
    let reads = transactions.iter().filter(|t| t["type"] == "read").count();
    let summary = format!(
        "AXI decode: {} write transactions, {} read transactions, {} protocol violation(s) on prefix '{}'",
        writes,
        reads,
        violations.len(),
        axi_prefix
    );

    json!({
        "transactions": transactions,
        "violations":   violations,
        "summary":      summary,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn evt(t: f64, v: &str) -> Value {
        json!({"time": t, "value": v})
    }

    fn make_axi(events: Vec<(&str, Vec<(f64, &str)>)>) -> Map<String, Value> {
        let mut m = Map::new();
        for (name, points) in events {
            m.insert(
                name.to_string(),
                Value::Array(points.into_iter().map(|(t, v)| evt(t, v)).collect()),
            );
        }
        m
    }

    #[test]
    fn decode_single_write_transaction() {
        // Single AW + W + B handshake at distinct clock edges.
        let events = make_axi(vec![
            (
                "aclk",
                vec![
                    (0.0, "0"),
                    (5.0, "1"),
                    (10.0, "0"),
                    (15.0, "1"),
                    (20.0, "0"),
                    (25.0, "1"),
                    (30.0, "0"),
                    (35.0, "1"),
                ],
            ),
            ("m_axi_awvalid", vec![(0.0, "0"), (5.0, "1"), (6.0, "0")]),
            ("m_axi_awready", vec![(0.0, "1")]),
            ("m_axi_awaddr", vec![(0.0, "00"), (5.0, "42")]),
            ("m_axi_wvalid", vec![(0.0, "0"), (15.0, "1"), (16.0, "0")]),
            ("m_axi_wready", vec![(0.0, "1")]),
            ("m_axi_bvalid", vec![(0.0, "0"), (25.0, "1"), (26.0, "0")]),
            ("m_axi_bready", vec![(0.0, "1")]),
        ]);
        let result = decode_axi(&events, "m_axi_", Some("aclk"), 100);
        let txns = result["transactions"].as_array().unwrap();
        assert_eq!(txns.len(), 1);
        assert_eq!(txns[0]["type"], "write");
        assert_eq!(txns[0]["status"], "ok");
        assert_eq!(result["violations"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn detect_handshake_timeout() {
        // VALID stays high forever, READY never asserts → timeout violation.
        let mut clk_events = Vec::new();
        for i in 0..50 {
            let t = i as f64 * 5.0;
            clk_events.push((t, if i % 2 == 0 { "0" } else { "1" }));
        }
        let events = make_axi(vec![
            ("aclk", clk_events),
            ("m_axi_awvalid", vec![(0.0, "1")]),
            ("m_axi_awready", vec![(0.0, "0")]),
        ]);
        let result = decode_axi(&events, "m_axi_", Some("aclk"), 5);
        let violations = result["violations"].as_array().unwrap();
        assert!(violations.len() >= 1);
        assert_eq!(violations[0]["violation_type"], "handshake_timeout");
    }
}
