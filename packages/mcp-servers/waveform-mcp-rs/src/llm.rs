//! Minimal Anthropic non-stream client.
//!
//! Mirrors the auth modes that `vibe4fpga_llm_client::ClaudeAdapter` supports
//! (claude.py post-fix):
//!
//! * `ANTHROPIC_API_KEY`    — direct api.anthropic.com (x-api-key)
//! * `ANTHROPIC_AUTH_TOKEN` — Bearer-token proxy / gateway (Claude Code OAuth)
//! * `ANTHROPIC_BASE_URL`   — override endpoint (third-party proxies)
//!
//! Precedence when both API_KEY and AUTH_TOKEN are set: API_KEY wins,
//! matching the Python SDK + our patched adapter.
//!
//! The non-stream `/v1/messages` response carries `content: [{type, text?, ...}]`.
//! We join `type == "text"` blocks and ignore `thinking` blocks — this is the
//! same shape that bit us in Python before claude.py was patched.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;

const DEFAULT_BASE_URL: &str = "https://api.anthropic.com";
const ANTHROPIC_VERSION: &str = "2023-06-01";

#[derive(Clone, Debug)]
enum Auth {
    ApiKey(String),
    Bearer(String),
}

#[derive(Clone, Debug)]
pub struct AnthropicClient {
    client: reqwest::Client,
    base_url: String,
    auth: Auth,
    model: String,
}

#[derive(Serialize)]
struct ChatMessage<'a> {
    role: &'a str,
    content: &'a str,
}

#[derive(Deserialize)]
struct ContentBlock {
    #[serde(rename = "type")]
    block_type: String,
    #[serde(default)]
    text: Option<String>,
}

#[derive(Deserialize)]
struct MessagesResponse {
    #[serde(default)]
    content: Vec<ContentBlock>,
}

impl AnthropicClient {
    /// Build a client from `ANTHROPIC_*` env vars. Returns Err when no auth
    /// credentials are configured.
    pub fn from_env() -> Result<Self> {
        let api_key = std::env::var("ANTHROPIC_API_KEY").ok().filter(|s| !s.is_empty());
        let auth_token = std::env::var("ANTHROPIC_AUTH_TOKEN").ok().filter(|s| !s.is_empty());
        let base_url = std::env::var("ANTHROPIC_BASE_URL")
            .ok()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| DEFAULT_BASE_URL.to_string());

        let auth = if let Some(k) = api_key {
            Auth::ApiKey(k)
        } else if let Some(t) = auth_token {
            Auth::Bearer(t)
        } else {
            bail!(
                "Neither ANTHROPIC_API_KEY nor ANTHROPIC_AUTH_TOKEN is set. \
                 Configure one to enable debug_waveform's LLM root-cause step."
            );
        };

        Ok(AnthropicClient {
            client: reqwest::Client::builder()
                .user_agent(concat!("waveform-mcp-rs/", env!("CARGO_PKG_VERSION")))
                .build()
                .context("reqwest client build failed")?,
            base_url: base_url.trim_end_matches('/').to_string(),
            auth,
            model: model_from_key(
                std::env::var("VIBE4FPGA_LLM")
                    .unwrap_or_else(|_| "claude".to_string())
                    .as_str(),
            ),
        })
    }

    pub async fn complete(
        &self,
        system: &str,
        user: &str,
        temperature: f64,
        max_tokens: u32,
    ) -> Result<String> {
        let url = format!("{}/v1/messages", self.base_url);

        let mut payload = serde_json::Map::new();
        payload.insert("model".into(), json!(self.model));
        payload.insert("max_tokens".into(), json!(max_tokens));
        payload.insert("temperature".into(), json!(temperature));
        payload.insert(
            "messages".into(),
            json!([ChatMessage { role: "user", content: user }]),
        );
        if !system.is_empty() {
            payload.insert("system".into(), json!(system));
        }

        let mut req = self
            .client
            .post(&url)
            .header("anthropic-version", ANTHROPIC_VERSION)
            .header("content-type", "application/json")
            .json(&payload);
        match &self.auth {
            Auth::ApiKey(k) => req = req.header("x-api-key", k),
            Auth::Bearer(t) => req = req.header("authorization", format!("Bearer {}", t)),
        }

        let resp = req.send().await.context("Anthropic request failed")?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            bail!("Anthropic API returned HTTP {}: {}", status, body);
        }
        let parsed: MessagesResponse = resp.json().await.context("invalid Anthropic JSON")?;

        let mut text = String::new();
        for block in parsed.content {
            if block.block_type == "text" {
                if let Some(t) = block.text {
                    text.push_str(&t);
                }
            }
        }
        Ok(text)
    }
}

fn model_from_key(key: &str) -> String {
    match key.to_lowercase().as_str() {
        "claude" | "claude-sonnet" => "claude-sonnet-4-6".to_string(),
        "claude-opus" => "claude-opus-4-6".to_string(),
        "claude-haiku" => "claude-haiku-4-5-20251001".to_string(),
        _ => "claude-sonnet-4-6".to_string(),
    }
}

/// Extract JSON from an LLM response, tolerating markdown code fences,
/// surrounding prose, and partial output. Mirrors the robust parser we put
/// in the Python `_llm.py` modules after bug #8.
pub fn parse_json_response(text: &str) -> Result<serde_json::Value> {
    // 1. Pull the inside of any ``` … ``` fence (works even when wrapped in prose).
    if let Some(fence_match) = find_fenced(text) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(fence_match.trim()) {
            return Ok(v);
        }
    }

    // 2. Scan for a balanced top-level [ ... ] or { ... } and try that.
    for (open_ch, close_ch) in [('[', ']'), ('{', '}')] {
        if let Some(slice) = scan_balanced(text, open_ch, close_ch) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(slice) {
                return Ok(v);
            }
        }
    }

    // 3. Last-resort: strip head/tail fences and try.
    let mut cleaned = text.trim().to_string();
    cleaned = strip_leading_fence(&cleaned).to_string();
    cleaned = strip_trailing_fence(&cleaned).to_string();
    serde_json::from_str(cleaned.trim()).context("unable to extract JSON from LLM response")
}

fn find_fenced(text: &str) -> Option<&str> {
    let bytes = text.as_bytes();
    let mut i = 0usize;
    while i + 3 <= bytes.len() {
        if &bytes[i..i + 3] == b"```" {
            // Skip optional language tag and newline
            let mut j = i + 3;
            while j < bytes.len() && bytes[j].is_ascii_alphabetic() {
                j += 1;
            }
            if j < bytes.len() && (bytes[j] == b'\n' || bytes[j] == b'\r') {
                j += 1;
            }
            // Find closing ```
            let body_start = j;
            while j + 3 <= bytes.len() {
                if &bytes[j..j + 3] == b"```" {
                    return Some(&text[body_start..j]);
                }
                j += 1;
            }
            return None;
        }
        i += 1;
    }
    None
}

fn scan_balanced(text: &str, open_ch: char, close_ch: char) -> Option<&str> {
    let bytes = text.as_bytes();
    let open_byte = open_ch as u8;
    let close_byte = close_ch as u8;
    let start = bytes.iter().position(|&b| b == open_byte)?;

    let mut depth = 0i32;
    let mut in_string = false;
    let mut escape = false;
    for (idx, &b) in bytes.iter().enumerate().skip(start) {
        if escape {
            escape = false;
            continue;
        }
        if b == b'\\' {
            escape = true;
            continue;
        }
        if b == b'"' {
            in_string = !in_string;
            continue;
        }
        if in_string {
            continue;
        }
        if b == open_byte {
            depth += 1;
        } else if b == close_byte {
            depth -= 1;
            if depth == 0 {
                return Some(&text[start..=idx]);
            }
        }
    }
    None
}

fn strip_leading_fence(s: &str) -> &str {
    let trimmed = s.trim_start();
    if let Some(rest) = trimmed.strip_prefix("```") {
        let after_tag = rest.trim_start_matches(|c: char| c.is_ascii_alphabetic());
        return after_tag.trim_start_matches(|c: char| c == '\n' || c == '\r');
    }
    s
}

fn strip_trailing_fence(s: &str) -> &str {
    s.trim_end().trim_end_matches("```").trim_end()
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_plain_json_array() {
        let v = parse_json_response(r#"[1, 2, 3]"#).unwrap();
        assert_eq!(v.as_array().unwrap().len(), 3);
    }

    #[test]
    fn parse_fenced_json() {
        let txt = "Some prose\n```json\n{\"k\": 1}\n```\nMore prose";
        let v = parse_json_response(txt).unwrap();
        assert_eq!(v["k"].as_i64(), Some(1));
    }

    #[test]
    fn parse_embedded_json_in_prose() {
        let txt = "> Note: this is wrapped in prose.\n[{\"x\": true}]\n\nMore prose follows.";
        let v = parse_json_response(txt).unwrap();
        assert_eq!(v.as_array().unwrap().len(), 1);
    }

    #[test]
    fn parse_balanced_with_quoted_brackets() {
        let txt = r#"prelude {"key": "[bracket]inside", "n": 7} postlude"#;
        let v = parse_json_response(txt).unwrap();
        assert_eq!(v["n"].as_i64(), Some(7));
    }
}
