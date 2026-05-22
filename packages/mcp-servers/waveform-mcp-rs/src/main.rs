//! waveform-mcp-rs — MCP server for VCD/FST waveform analysis.
//!
//! Tools (post-merge with the old Python waveform-mcp):
//!   parse_waveform        — parse a waveform file and return metadata
//!   extract_signal_events — extract compressed events for signals in a time window
//!   get_signal_stats      — per-signal statistics (transitions, value distribution)
//!   summarize_waveform    — LLM-friendly structured summary (incl. L2/L3)
//!   decode_axi            — AXI4 5-channel state machine
//!   map_signal_to_rtl     — reverse-map a sim signal to RTL source location
//!   debug_waveform        — 5 detectors + LLM root-cause analysis

mod axi_decoder;
mod compressor;
mod detectors;
mod llm;
mod parser;
mod signal_map;
mod skill_debug_waveform;

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

use compressor::{compress_for_llm, compute_stats, l1_sample};
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
        let query = args.get("query").and_then(|v| v.as_str()).unwrap_or("");
        let token_budget = args
            .get("token_budget")
            .and_then(|v| v.as_u64())
            .unwrap_or(4000) as usize;

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

        // Always include the LLM-friendly compressed view (Python summarize_for_llm
        // contract). Cheap to compute; harmless when callers ignore it.
        let compressed = compress_for_llm(&meta, query, token_budget);

        json!({
            "format":              meta.format,
            "duration_ns":         meta.duration_ns,
            "total_signals":       meta.signals.len(),
            "clock_count":         clocks.len(),
            "clocks":              clocks,
            "most_active_signals": most_active,
            "signals":             all_signals,
            "metadata_summary":    compressed.metadata_summary,
            "clock_summaries":     compressed.clock_summaries,
            "event_narrative":     compressed.event_narrative,
        })
    }

    fn tool_decode_axi(&self, args: &Map<String, Value>) -> Value {
        let file_path = match args.get("file_path").and_then(|v| v.as_str()) {
            Some(p) => p.to_string(),
            None => return json!({"error": "Missing required argument: file_path"}),
        };
        let axi_prefix = args.get("axi_prefix").and_then(|v| v.as_str()).unwrap_or("");
        let clock_name = args.get("clock_name").and_then(|v| v.as_str());
        let timeout_cycles = args
            .get("timeout_cycles")
            .and_then(|v| v.as_u64())
            .unwrap_or(100) as u32;
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

        // Build compressed signal_events restricted to the requested window.
        let mut signal_events = Map::new();
        for (name, sig) in &meta.signals {
            let filtered: Vec<(f64, String)> = sig
                .tv
                .iter()
                .filter(|(t, _)| *t >= time_start_ns && *t <= end)
                .cloned()
                .collect();
            signal_events.insert(name.clone(), Value::Array(l1_sample(&filtered)));
        }

        axi_decoder::decode_axi(&signal_events, axi_prefix, clock_name, timeout_cycles)
    }

    fn tool_map_signal_to_rtl(&self, args: &Map<String, Value>) -> Value {
        let waveform_path = args.get("waveform_path").and_then(|v| v.as_str()).unwrap_or("");
        let signal_name = match args.get("signal_name").and_then(|v| v.as_str()) {
            Some(s) => s,
            None => return json!({"error": "Missing required argument: signal_name"}),
        };
        let rtl_root = args.get("rtl_root").and_then(|v| v.as_str());
        signal_map::map_signal_to_rtl(waveform_path, signal_name, rtl_root)
    }

    async fn tool_debug_waveform(&self, args: &Map<String, Value>) -> Value {
        // Accept both `waveform_path` (Rust convention) and `vcd_path` (Python tool legacy).
        let waveform_path = args
            .get("waveform_path")
            .or_else(|| args.get("vcd_path"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let waveform_path = match waveform_path {
            Some(p) if !p.is_empty() => p,
            _ => return json!({"error": "Missing required argument: waveform_path (or vcd_path)"}),
        };

        let meta = match self.load(&waveform_path) {
            Ok(m) => m,
            Err(e) => return json!({"error": e}),
        };

        let input = skill_debug_waveform::DebugInput {
            query: args
                .get("query")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            axi_prefix: args
                .get("axi_prefix")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            token_budget: args
                .get("token_budget")
                .and_then(|v| v.as_u64())
                .unwrap_or(4000) as usize,
            signal_whitelist: args
                .get("signals")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                }),
            ..Default::default()
        };

        skill_debug_waveform::run(&meta, input).await
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
                        "file_path": {"type": "string", "description": "Absolute path to .vcd or .fst waveform file"}
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
                        "file_path": {"type": "string", "description": "Absolute path to waveform file"},
                        "signals": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Signal names to extract (also accepts comma-separated string)"
                        },
                        "time_start_ns": {"type": "number", "description": "Window start in nanoseconds (default: 0)"},
                        "time_end_ns": {"type": "number", "description": "Window end in nanoseconds (default: end of simulation)"}
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
                        "file_path": {"type": "string", "description": "Absolute path to waveform file"},
                        "signal_name": {"type": "string", "description": "Fully-qualified signal name (e.g. tb.dut.clk)"}
                    }),
                    &["file_path", "signal_name"],
                ),
            },
            Tool {
                name: "summarize_waveform".into(),
                description: "LLM-friendly structured summary: clock frequencies, most-active signals, \
                              signal list, plus query-aware compressed event narrative."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "file_path": {"type": "string", "description": "Absolute path to waveform file"},
                        "max_signals": {"type": "integer", "description": "Maximum number of data signals in output (default: 50)"},
                        "query": {"type": "string", "description": "Optional engineer query — drives L3 query-aware pruning"},
                        "token_budget": {"type": "integer", "description": "Token budget for the compressed event narrative (default: 4000)"}
                    }),
                    &["file_path"],
                ),
            },
            Tool {
                name: "decode_axi".into(),
                description: "AXI4 5-channel state-machine decoder. Detects handshake completions \
                              and protocol violations (timeout, W-before-AW, missing responses) \
                              for a configurable signal prefix."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "file_path": {"type": "string", "description": "Absolute path to waveform file"},
                        "axi_prefix": {"type": "string", "description": "Signal prefix (e.g. m_axi_) — empty disables prefixing"},
                        "clock_name": {"type": "string", "description": "Clock signal name (auto-detected if omitted)"},
                        "timeout_cycles": {"type": "integer", "description": "Cycles before VALID-without-READY counts as timeout"},
                        "time_start_ns": {"type": "number", "description": "Window start in nanoseconds (default: 0)"},
                        "time_end_ns": {"type": "number", "description": "Window end in nanoseconds (default: end of simulation)"}
                    }),
                    &["file_path"],
                ),
            },
            Tool {
                name: "map_signal_to_rtl".into(),
                description: "Reverse-map a simulation signal name to its RTL source location. \
                              Searches the given RTL root for files containing the base signal name."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "waveform_path": {"type": "string", "description": "Path to the waveform file (for context)"},
                        "signal_name": {"type": "string", "description": "Fully-qualified simulation signal name"},
                        "rtl_root": {"type": "string", "description": "Optional RTL project root for source search"}
                    }),
                    &["waveform_path", "signal_name"],
                ),
            },
            Tool {
                name: "debug_waveform".into(),
                description: "Run 5 anomaly detectors (glitch, X/Z, CDC, AXI, handshake timeout) on \
                              a waveform and ask the LLM for a per-anomaly root cause + RTL fix. \
                              Requires ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN for the LLM step."
                    .into(),
                input_schema: make_schema(
                    json!({
                        "waveform_path": {"type": "string", "description": "Absolute path to .vcd / .fst waveform"},
                        "query": {"type": "string", "description": "Engineer's question / focus area — drives L3 pruning"},
                        "axi_prefix": {"type": "string", "description": "AXI signal prefix (empty disables AXI detector)"},
                        "token_budget": {"type": "integer", "description": "Max tokens for the LLM context bundle"},
                        "signals": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional signal whitelist; clocks are always retained"
                        }
                    }),
                    &["waveform_path"],
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
        let server = self.clone();
        let name = req.name.to_string();
        let empty_map = Map::new();
        let args = req.arguments.unwrap_or(empty_map);

        async move {
            let output = match name.as_str() {
                "parse_waveform" => server.tool_parse_waveform(&args),
                "extract_signal_events" => server.tool_extract_signal_events(&args),
                "get_signal_stats" => server.tool_get_signal_stats(&args),
                "summarize_waveform" => server.tool_summarize_waveform(&args),
                "decode_axi" => server.tool_decode_axi(&args),
                "map_signal_to_rtl" => server.tool_map_signal_to_rtl(&args),
                "debug_waveform" => server.tool_debug_waveform(&args).await,
                other => {
                    return Err(McpError::invalid_params(
                        format!("Unknown tool: {}", other),
                        None,
                    ));
                }
            };

            let is_error = output.get("error").is_some();
            let text = serde_json::to_string_pretty(&output).unwrap_or_else(|e| {
                json!({"error": format!("Serialization error: {}", e)}).to_string()
            });

            Ok(CallToolResult {
                content: vec![Content::text(text)],
                is_error: Some(is_error),
            })
        }
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> anyhow::Result<()> {
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
