//! waveform-mcp-rs — MCP server for VCD/FST waveform file analysis.
//!
//! Exposes four MCP tools:
//!   - parse_waveform        : parse a waveform file and return metadata
//!   - extract_signal_events : extract compressed events for signals in a time window
//!   - get_signal_stats      : per-signal statistics (transitions, value distribution)
//!   - summarize_waveform    : LLM-friendly structured summary

mod compressor;
mod parser;

use std::collections::HashMap;
use std::future::Future;
use std::sync::{Arc, Mutex};

use rmcp::{
    model::{
        CallToolRequestParam, CallToolResult, Content, Implementation, ListToolsResult,
        PaginatedRequestParam, ProtocolVersion, ServerCapabilities, ServerInfo, Tool,
    },
    service::{RequestContext, RoleServer},
    Error as McpError, ServerHandler, ServiceExt,
};
use serde_json::{json, Map, Value};
use tracing_subscriber::EnvFilter;

use compressor::{compute_stats, l1_sample};
use parser::{parse_waveform, WaveformMeta};

// ── LRU cache ────────────────────────────────────────────────────────────────

const CACHE_MAX: usize = 8;

struct Cache {
    data: HashMap<String, WaveformMeta>,
    order: Vec<String>,
}

impl Cache {
    fn new() -> Self {
        Cache {
            data: HashMap::new(),
            order: Vec::new(),
        }
    }

    fn get(&mut self, key: &str) -> Option<&WaveformMeta> {
        if self.data.contains_key(key) {
            self.order.retain(|k| k != key);
            self.order.push(key.to_string());
            self.data.get(key)
        } else {
            None
        }
    }

    fn insert(&mut self, key: String, meta: WaveformMeta) {
        self.data.insert(key.clone(), meta);
        self.order.push(key);
        while self.order.len() > CACHE_MAX {
            if let Some(evict) = self.order.first().cloned() {
                self.order.remove(0);
                self.data.remove(&evict);
            }
        }
    }
}

// ── Helper: build JSON Schema Arc<Map> for tool input ────────────────────────

fn make_schema(properties: Value, required: &[&str]) -> Arc<Map<String, Value>> {
    let obj = json!({
        "type": "object",
        "properties": properties,
        "required": required,
    });
    Arc::new(obj.as_object().cloned().unwrap_or_default())
}

// ── Server ───────────────────────────────────────────────────────────────────

#[derive(Clone)]
struct WaveformServer {
    cache: Arc<Mutex<Cache>>,
}

impl WaveformServer {
    fn new() -> Self {
        WaveformServer {
            cache: Arc::new(Mutex::new(Cache::new())),
        }
    }

    fn load(&self, file_path: &str) -> Result<WaveformMeta, String> {
        let mut cache = self.cache.lock().unwrap();
        if let Some(meta) = cache.get(file_path) {
            return Ok(meta.clone());
        }
        match parse_waveform(file_path) {
            Ok(meta) => {
                let result = meta.clone();
                cache.insert(file_path.to_string(), meta);
                Ok(result)
            }
            Err(e) => Err(e.to_string()),
        }
    }

    // ── Tool implementations ─────────────────────────────────────────────────

    fn tool_parse_waveform(&self, args: &Map<String, Value>) -> Value {
        let file_path = match args.get("file_path").and_then(|v| v.as_str()) {
            Some(p) => p.to_string(),
            None => return json!({"error": "Missing required argument: file_path"}),
        };
        match self.load(&file_path) {
            Ok(meta) => meta.to_summary(),
            Err(e) => json!({"error": e}),
        }
    }

    fn tool_extract_signal_events(&self, args: &Map<String, Value>) -> Value {
        let file_path = match args.get("file_path").and_then(|v| v.as_str()) {
            Some(p) => p.to_string(),
            None => return json!({"error": "Missing required argument: file_path"}),
        };

        let signal_names: Vec<String> = match args.get("signals") {
            Some(Value::Array(arr)) => arr
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect(),
            Some(Value::String(s)) => s.split(',').map(|x| x.trim().to_string()).collect(),
            _ => {
                return json!({"error": "Missing required argument: signals"})
            }
        };

        let time_start_ns = args
            .get("time_start_ns")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let time_end_ns = args.get("time_end_ns").and_then(|v| v.as_f64());
        let end = time_end_ns.unwrap_or(f64::INFINITY);

        let meta = match self.load(&file_path) {
            Ok(m) => m,
            Err(e) => return json!({"error": e}),
        };

        let mut result = Map::new();
        for name in &signal_names {
            let events = match meta.signals.get(name) {
                Some(sig) => {
                    let filtered: Vec<(f64, String)> = sig
                        .tv
                        .iter()
                        .filter(|(t, _)| *t >= time_start_ns && *t <= end)
                        .cloned()
                        .collect();
                    json!(l1_sample(&filtered))
                }
                None => json!([]),
            };
            result.insert(name.clone(), events);
        }
        Value::Object(result)
    }

    fn tool_get_signal_stats(&self, args: &Map<String, Value>) -> Value {
        let file_path = match args.get("file_path").and_then(|v| v.as_str()) {
            Some(p) => p.to_string(),
            None => return json!({"error": "Missing required argument: file_path"}),
        };
        let signal_name = match args.get("signal_name").and_then(|v| v.as_str()) {
            Some(s) => s.to_string(),
            None => return json!({"error": "Missing required argument: signal_name"}),
        };

        let meta = match self.load(&file_path) {
            Ok(m) => m,
            Err(e) => return json!({"error": e}),
        };

        let sig = match meta.signals.get(&signal_name) {
            Some(s) => s,
            None => return json!({"error": format!("Signal '{}' not found", signal_name)}),
        };

        let stats = compute_stats(&sig.tv);
        json!({
            "name":               sig.name,
            "width":              sig.width,
            "scope":              sig.scope,
            "transition_count":   stats.transition_count,
            "first_event_ns":     stats.first_time_ns,
            "last_event_ns":      stats.last_time_ns,
            "is_clock":           sig.is_clock(),
            "clock_period_ns":    if sig.is_clock() { sig.clock_period_ns() } else { 0.0 },
            "value_distribution": stats.value_counts,
        })
    }

    fn tool_summarize_waveform(&self, args: &Map<String, Value>) -> Value {
        let file_path = match args.get("file_path").and_then(|v| v.as_str()) {
            Some(p) => p.to_string(),
            None => return json!({"error": "Missing required argument: file_path"}),
        };
        let max_signals = args
            .get("max_signals")
            .and_then(|v| v.as_u64())
            .unwrap_or(50) as usize;

        let meta = match self.load(&file_path) {
            Ok(m) => m,
            Err(e) => return json!({"error": e}),
        };

        let clocks = meta.clocks();

        let mut data_sigs: Vec<_> = meta
            .signals
            .values()
            .filter(|s| !s.is_clock())
            .collect();
        data_sigs.sort_by(|a, b| b.tv.len().cmp(&a.tv.len()));

        let most_active: Vec<_> = data_sigs
            .iter()
            .take(10)
            .map(|s| {
                json!({
                    "name":             s.name,
                    "width":            s.width,
                    "transition_count": s.tv.len(),
                })
            })
            .collect();

        let all_signals: Vec<_> = data_sigs
            .iter()
            .take(max_signals)
            .map(|s| {
                json!({
                    "name":        s.name,
                    "width":       s.width,
                    "scope":       s.scope,
                    "transitions": s.tv.len(),
                })
            })
            .collect();

        json!({
            "format":              meta.format,
            "duration_ns":         meta.duration_ns,
            "total_signals":       meta.signals.len(),
            "clock_count":         clocks.len(),
            "clocks":              clocks,
            "most_active_signals": most_active,
            "signals":             all_signals,
        })
    }
}

// ── MCP ServerHandler implementation ─────────────────────────────────────────

impl ServerHandler for WaveformServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            protocol_version: ProtocolVersion::V_2024_11_05,
            capabilities: ServerCapabilities::builder().enable_tools().build(),
            server_info: Implementation {
                name: "waveform-mcp-rs".into(),
                version: env!("CARGO_PKG_VERSION").into(),
            },
            instructions: None,
        }
    }

    fn list_tools(
        &self,
        _request: PaginatedRequestParam,
        _ctx: RequestContext<RoleServer>,
    ) -> impl Future<Output = Result<ListToolsResult, McpError>> + Send + '_ {
        let tools = vec![
            Tool {
                name: "parse_waveform".into(),
                description: "Parse a VCD or FST waveform file. Returns metadata: format, \
                              duration, signal list, and detected clocks."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to .vcd or .fst waveform file"
                        }
                    }),
                    &["file_path"],
                ),
            },
            Tool {
                name: "extract_signal_events".into(),
                description: "Extract L1-compressed time-value events for specified signals \
                              in a time window. Stable regions are collapsed to save tokens."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to waveform file"
                        },
                        "signals": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Signal names to extract (also accepts comma-separated string)"
                        },
                        "time_start_ns": {
                            "type": "number",
                            "description": "Window start in nanoseconds (default: 0)"
                        },
                        "time_end_ns": {
                            "type": "number",
                            "description": "Window end in nanoseconds (default: end of simulation)"
                        }
                    }),
                    &["file_path", "signals"],
                ),
            },
            Tool {
                name: "get_signal_stats".into(),
                description: "Get statistics for a single signal: transition count, \
                              first/last event time, clock detection, and value distribution."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to waveform file"
                        },
                        "signal_name": {
                            "type": "string",
                            "description": "Fully-qualified signal name (e.g. tb.dut.clk)"
                        }
                    }),
                    &["file_path", "signal_name"],
                ),
            },
            Tool {
                name: "summarize_waveform".into(),
                description: "Generate an LLM-friendly structured summary: clock frequencies, \
                              most-active signals, and full signal list."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to waveform file"
                        },
                        "max_signals": {
                            "type": "integer",
                            "description": "Maximum number of data signals in output (default: 50)"
                        }
                    }),
                    &["file_path"],
                ),
            },
        ];

        std::future::ready(Ok(ListToolsResult {
            tools,
            next_cursor: None,
        }))
    }

    fn call_tool(
        &self,
        req: CallToolRequestParam,
        _ctx: RequestContext<RoleServer>,
    ) -> impl Future<Output = Result<CallToolResult, McpError>> + Send + '_ {
        let empty_map = Map::new();
        let args = req.arguments.as_ref().unwrap_or(&empty_map);

        let output = match req.name.as_ref() {
            "parse_waveform" => self.tool_parse_waveform(args),
            "extract_signal_events" => self.tool_extract_signal_events(args),
            "get_signal_stats" => self.tool_get_signal_stats(args),
            "summarize_waveform" => self.tool_summarize_waveform(args),
            other => {
                return std::future::ready(Err(McpError::invalid_params(
                    format!("Unknown tool: {}", other),
                    None,
                )))
            }
        };

        let is_error = output.get("error").is_some();
        let text = serde_json::to_string_pretty(&output).unwrap_or_else(|e| {
            json!({"error": format!("Serialization error: {}", e)}).to_string()
        });

        std::future::ready(Ok(CallToolResult {
            content: vec![Content::text(text)],
            is_error: Some(is_error),
        }))
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Direct logs to stderr; stdout is reserved for the MCP JSON-RPC stream.
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .with_writer(std::io::stderr)
        .init();

    tracing::info!("waveform-mcp-rs starting");

    WaveformServer::new()
        .serve(rmcp::transport::stdio())
        .await?
        .waiting()
        .await?;

    Ok(())
}
