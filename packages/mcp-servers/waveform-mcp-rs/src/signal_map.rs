//! Reverse-map a simulation signal name to its RTL source location.
//!
//! Port of `map_signal_to_rtl` from the Python server.py. Walks an RTL root
//! directory, opens every `.v`/`.sv` file under 512 KB, runs a word-boundary
//! regex for the bare signal name, returns up to 10 hits.

use serde_json::{json, Value};
use std::fs;

const MAX_FILE_BYTES: u64 = 512 * 1024;
const MAX_HITS: usize = 10;

pub fn map_signal_to_rtl(
    waveform_path: &str,
    signal_name: &str,
    rtl_root: Option<&str>,
) -> Value {
    let _ = waveform_path; // kept for parity with the Python signature

    let root = match rtl_root {
        Some(r) if !r.is_empty() => r,
        _ => {
            return json!({
                "status":      "no_rtl_root",
                "signal":      signal_name,
            });
        }
    };

    let base_name = signal_name.rsplit('.').next().unwrap_or(signal_name);
    let escaped = regex::escape(base_name);
    let pattern = match regex::Regex::new(&format!(r"\b{}\b", escaped)) {
        Ok(p) => p,
        Err(e) => {
            return json!({"error": format!("invalid signal regex: {}", e), "signal": signal_name});
        }
    };

    let mut hits: Vec<Value> = Vec::new();

    'outer: for entry in walkdir::WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        if !entry.file_type().is_file() {
            continue;
        }
        let path = entry.path();
        let ext_ok = match path.extension().and_then(|e| e.to_str()) {
            Some(ext) => {
                let lc = ext.to_lowercase();
                lc == "v" || lc == "sv"
            }
            None => false,
        };
        if !ext_ok {
            continue;
        }
        let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
        if size > MAX_FILE_BYTES {
            continue;
        }

        let content = match fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => continue,
        };

        for (idx, line) in content.lines().enumerate() {
            if pattern.is_match(line) {
                hits.push(json!({
                    "file":    path.display().to_string(),
                    "line":    idx + 1,
                    "context": line.trim(),
                }));
                if hits.len() >= MAX_HITS {
                    break 'outer;
                }
            }
        }
    }

    json!({
        "signal":    signal_name,
        "base_name": base_name,
        "rtl_hits":  hits,
        "status":    if hits.is_empty() { "not_found" } else { "found" },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn finds_signal_in_rtl_file() {
        let tmp = std::env::temp_dir().join(format!(
            "waveform_signal_map_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let rtl_path = tmp.join("dut.v");
        let mut f = std::fs::File::create(&rtl_path).unwrap();
        writeln!(f, "module dut(input wire clk);").unwrap();
        writeln!(f, "  reg [3:0] count;").unwrap();
        writeln!(f, "  always @(posedge clk) count <= count + 1;").unwrap();
        writeln!(f, "endmodule").unwrap();
        drop(f);

        let result = map_signal_to_rtl("dummy.vcd", "tb.dut.count", Some(tmp.to_str().unwrap()));
        assert_eq!(result["status"], "found");
        let hits = result["rtl_hits"].as_array().unwrap();
        assert!(hits.len() >= 1);
        assert!(hits[0]["context"].as_str().unwrap().contains("count"));

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn returns_no_rtl_root_when_missing() {
        let result = map_signal_to_rtl("dummy.vcd", "tb.x", None);
        assert_eq!(result["status"], "no_rtl_root");
    }
}
