//! VCD/FST waveform parser with timescale normalization.
//!
//! VCD is parsed natively using the `vcd` crate.
//! FST is handled via GTKWave's `fst2vcd` conversion tool.

use std::collections::HashMap;
use std::fs::File;
use std::io::BufReader;

use anyhow::{bail, Context, Result};
use serde::Serialize;
use vcd::{Command, IdCode, Parser, ScopeItem, TimescaleUnit, Value};

// ── Signal data model ────────────────────────────────────────────────────────

#[derive(Clone, Debug, Serialize)]
pub struct SignalTrace {
    pub name: String,
    pub width: u32,
    pub scope: String,
    /// (time_ns, value_str) pairs sorted by time
    pub tv: Vec<(f64, String)>,
}

impl SignalTrace {
    /// Heuristic: regular binary toggle with ≥20 transitions and consistent period.
    pub fn is_clock(&self) -> bool {
        if self.width != 1 || self.tv.len() < 20 {
            return false;
        }
        let vals: Vec<&str> = self.tv.iter().take(40).map(|(_, v)| v.as_str()).collect();
        if vals.len() < 11 {
            return false;
        }
        // Must alternate between 0 and 1
        let alternating = vals.windows(2).take(10).all(|w| w[0] != w[1]);
        if !alternating {
            return false;
        }
        // Period must be consistent (std < 5% of mean)
        let periods: Vec<f64> = self
            .tv
            .windows(2)
            .take(20)
            .map(|w| w[1].0 - w[0].0)
            .collect();
        if periods.is_empty() {
            return false;
        }
        let mean = periods.iter().sum::<f64>() / periods.len() as f64;
        if mean <= 0.0 {
            return false;
        }
        let variance =
            periods.iter().map(|&p| (p - mean).powi(2)).sum::<f64>() / periods.len() as f64;
        (variance.sqrt() / mean) < 0.05
    }

    /// Full clock period in ns (2 × average half-period).
    pub fn clock_period_ns(&self) -> f64 {
        if self.tv.len() < 2 {
            return 0.0;
        }
        let half_periods: Vec<f64> = self
            .tv
            .windows(2)
            .take(10)
            .map(|w| w[1].0 - w[0].0)
            .collect();
        if half_periods.is_empty() {
            return 0.0;
        }
        2.0 * half_periods.iter().sum::<f64>() / half_periods.len() as f64
    }
}

// ── Waveform metadata ────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct WaveformMeta {
    pub format: String,
    pub file_path: String,
    pub duration_ns: f64,
    pub signals: HashMap<String, SignalTrace>,
}

impl WaveformMeta {
    pub fn clocks(&self) -> Vec<serde_json::Value> {
        let mut clocks: Vec<_> = self
            .signals
            .values()
            .filter(|s| s.is_clock())
            .map(|s| {
                let period = s.clock_period_ns();
                let freq_mhz = if period > 0.0 { 1000.0 / period } else { 0.0 };
                serde_json::json!({
                    "name": s.name,
                    "period_ns": (period * 1000.0).round() / 1000.0,
                    "freq_mhz":  (freq_mhz * 1000.0).round() / 1000.0,
                })
            })
            .collect();
        clocks.sort_by(|a, b| {
            a["name"]
                .as_str()
                .unwrap_or("")
                .cmp(b["name"].as_str().unwrap_or(""))
        });
        clocks
    }

    pub fn to_summary(&self) -> serde_json::Value {
        let mut names: Vec<&str> = self.signals.keys().map(|s| s.as_str()).collect();
        names.sort_unstable();

        let signals: Vec<_> = names
            .iter()
            .take(200)
            .map(|&n| {
                let s = &self.signals[n];
                serde_json::json!({
                    "name":  s.name,
                    "width": s.width,
                    "scope": s.scope,
                })
            })
            .collect();

        serde_json::json!({
            "format":       self.format,
            "duration_ns":  (self.duration_ns * 1000.0).round() / 1000.0,
            "signal_count": self.signals.len(),
            "clocks":       self.clocks(),
            "signals":      signals,
        })
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

fn timescale_to_ns(magnitude: u32, unit: TimescaleUnit) -> f64 {
    let factor = match unit {
        TimescaleUnit::FS => 1e-6,
        TimescaleUnit::PS => 1e-3,
        TimescaleUnit::NS => 1.0,
        TimescaleUnit::US => 1e3,
        TimescaleUnit::MS => 1e6,
        TimescaleUnit::S => 1e9,
    };
    magnitude as f64 * factor
}

fn value_to_char(v: &Value) -> char {
    match v {
        Value::V0 => '0',
        Value::V1 => '1',
        Value::X => 'x',
        Value::Z => 'z',
    }
}

/// Recursively walk the VCD scope tree and populate `out` with:
/// `IdCode → (full_hierarchical_name, scope_path, bit_width)`
fn collect_signals(
    items: &[ScopeItem],
    path: &str,
    out: &mut HashMap<IdCode, (String, String, u32)>,
) {
    for item in items {
        match item {
            ScopeItem::Scope(scope) => {
                let child_path = if path.is_empty() {
                    scope.identifier.clone()
                } else {
                    format!("{}.{}", path, scope.identifier)
                };
                collect_signals(&scope.items, &child_path, out);
            }
            ScopeItem::Var(var) => {
                let full_name = if path.is_empty() {
                    var.reference.clone()
                } else {
                    format!("{}.{}", path, var.reference)
                };
                out.insert(var.code, (full_name, path.to_string(), var.size));
            }
            _ => {}
        }
    }
}

// ── VCD parser ───────────────────────────────────────────────────────────────

pub fn parse_vcd(file_path: &str) -> Result<WaveformMeta> {
    let file =
        File::open(file_path).with_context(|| format!("Cannot open file: {}", file_path))?;
    let reader = BufReader::new(file);
    let mut parser = Parser::new(reader);

    let header = parser.parse_header().context("Failed to parse VCD header")?;

    let ts_factor_ns = match header.timescale {
        Some((magnitude, unit)) => timescale_to_ns(magnitude, unit),
        None => 1.0, // default: assume 1 ns
    };

    // Walk scope tree to collect signal metadata
    let mut id_to_info: HashMap<IdCode, (String, String, u32)> = HashMap::new();
    collect_signals(&header.items, "", &mut id_to_info);

    // Stream through simulation commands
    let mut tv_map: HashMap<IdCode, Vec<(f64, String)>> = HashMap::new();
    let mut current_time: u64 = 0;
    let mut end_time: u64 = 0;

    for cmd in parser {
        match cmd.context("Failed to parse VCD command")? {
            Command::Timestamp(t) => {
                current_time = t;
                if t > end_time {
                    end_time = t;
                }
            }
            Command::ChangeScalar(id, val) => {
                let t_ns = current_time as f64 * ts_factor_ns;
                tv_map
                    .entry(id)
                    .or_default()
                    .push((t_ns, value_to_char(&val).to_string()));
            }
            Command::ChangeVector(id, vals) => {
                let t_ns = current_time as f64 * ts_factor_ns;
                let val_str: String = vals.iter().map(|v| value_to_char(&v)).collect();
                tv_map.entry(id).or_default().push((t_ns, val_str));
            }
            Command::ChangeReal(id, val) => {
                let t_ns = current_time as f64 * ts_factor_ns;
                tv_map
                    .entry(id)
                    .or_default()
                    .push((t_ns, format!("{:.6}", val)));
            }
            _ => {}
        }
    }

    let duration_ns = end_time as f64 * ts_factor_ns;

    // Assemble final signals map
    let mut signals: HashMap<String, SignalTrace> = HashMap::new();
    for (id, (full_name, scope, width)) in id_to_info {
        let tv = tv_map.remove(&id).unwrap_or_default();
        signals.insert(
            full_name.clone(),
            SignalTrace {
                name: full_name,
                width,
                scope,
                tv,
            },
        );
    }

    Ok(WaveformMeta {
        format: "vcd".to_string(),
        file_path: file_path.to_string(),
        duration_ns,
        signals,
    })
}

// ── FST parser (via fst2vcd) ─────────────────────────────────────────────────

pub fn parse_fst(file_path: &str) -> Result<WaveformMeta> {
    let available = std::process::Command::new("which")
        .arg("fst2vcd")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    if !available {
        bail!(
            "fst2vcd not found. Install GTKWave (e.g. `brew install gtkwave`) to enable FST parsing."
        );
    }

    let tmp_path = std::env::temp_dir().join(format!("waveform_mcp_{}.vcd", std::process::id()));
    let tmp_str = tmp_path.to_str().unwrap();

    let out = std::process::Command::new("fst2vcd")
        .args(["-o", tmp_str, file_path])
        .output()
        .context("Failed to run fst2vcd")?;

    if !out.status.success() {
        bail!(
            "fst2vcd conversion failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }

    let mut meta = parse_vcd(tmp_str)?;
    meta.format = "fst".to_string();
    meta.file_path = file_path.to_string();
    let _ = std::fs::remove_file(&tmp_path);

    Ok(meta)
}

// ── Auto-detect format ───────────────────────────────────────────────────────

pub fn parse_waveform(file_path: &str) -> Result<WaveformMeta> {
    let ext = std::path::Path::new(file_path)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_lowercase());

    match ext.as_deref() {
        Some("vcd") => parse_vcd(file_path),
        Some("fst") => parse_fst(file_path),
        Some(e) => bail!(
            "Unsupported waveform format '.{}'. Supported: .vcd, .fst",
            e
        ),
        None => bail!("File has no extension; cannot determine waveform format"),
    }
}
