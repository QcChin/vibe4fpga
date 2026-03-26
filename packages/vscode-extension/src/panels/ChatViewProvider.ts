import * as http from 'http';
import * as vscode from 'vscode';
import { McpManager, MCP_SERVICE_DEFS, McpStatus } from '../services/McpManager';

function nonce(): string {
    let s = '';
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) { s += chars[Math.floor(Math.random() * chars.length)]; }
    return s;
}

export class ChatViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'vibe4fpga.chatPanel';
    private _view?: vscode.WebviewView;

    constructor(
        private readonly _extensionUri: vscode.Uri,
        private readonly _mcpManager: McpManager,
    ) {}

    // ── WebviewViewProvider ──────────────────────────────────────────────────

    public resolveWebviewView(
        view: vscode.WebviewView,
        _ctx: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ): void {
        this._view = view;

        view.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri],
        };
        view.webview.html = this._html(view.webview);

        view.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.type) {
                case 'ready':
                    this._sendInit();
                    break;
                case 'sendMessage':
                    await this._handleChat(msg.text);
                    break;
                case 'spec2rtl':
                    await this._handleSpec2RTL(msg.spec);
                    break;
                case 'toggleMcp':
                    this._mcpManager.toggle(msg.id);
                    break;
                case 'saveConfig':
                    await this._saveConfig(msg.config);
                    break;
            }
        });
    }

    // ── Public API ───────────────────────────────────────────────────────────

    public focus(): void {
        this._view?.show(true);
    }

    public postMessage(msg: Record<string, unknown>): void {
        this._view?.webview.postMessage(msg);
    }

    public notifyMcpStatus(id: string, status: McpStatus): void {
        this._view?.webview.postMessage({ type: 'mcpStatus', id, status });
    }

    // ── Internal ─────────────────────────────────────────────────────────────

    private _sendInit(): void {
        const cfg = vscode.workspace.getConfiguration('vibe4fpga');
        this._view?.webview.postMessage({
            type:        'init',
            config: {
                backend:   cfg.get<string>('llm.backend',    'claude'),
                routerUrl: cfg.get<string>('llmRouter.url',  'http://localhost:8765'),
                apiKey:    cfg.get<string>('llm.apiKey',     ''),
            },
            mcpStatuses: this._mcpManager.getStatuses(),
        });
    }

    private async _saveConfig(cfg: { backend: string; routerUrl: string; apiKey: string }): Promise<void> {
        const config = vscode.workspace.getConfiguration('vibe4fpga');
        await config.update('llm.backend',   cfg.backend,   vscode.ConfigurationTarget.Global);
        await config.update('llmRouter.url', cfg.routerUrl, vscode.ConfigurationTarget.Global);
        if (cfg.apiKey) {
            await config.update('llm.apiKey', cfg.apiKey, vscode.ConfigurationTarget.Global);
        }
        this._view?.webview.postMessage({ type: 'configSaved' });
    }

    private async _handleChat(text: string): Promise<void> {
        const cfg       = vscode.workspace.getConfiguration('vibe4fpga');
        const routerUrl = cfg.get<string>('llmRouter.url', 'http://localhost:8765');
        const model     = cfg.get<string>('llm.backend',   'claude');

        const body = JSON.stringify({
            messages: [{ role: 'user', content: text }],
            model,
            stream: true,
        });

        return this._streamRequest(`${routerUrl}/chat/stream`, body);
    }

    private async _handleSpec2RTL(spec: string): Promise<void> {
        const cfg       = vscode.workspace.getConfiguration('vibe4fpga');
        const routerUrl = cfg.get<string>('llmRouter.url', 'http://localhost:8765');
        const model     = cfg.get<string>('llm.backend',   'claude');

        this._view?.webview.postMessage({ type: 'statusUpdate', text: 'Running Spec2RTL…' });

        const body = JSON.stringify({
            spec,
            model,
            project_path: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
        });

        try {
            const result = await this._postJson(`${routerUrl}/skill/spec2rtl`, body);
            this._view?.webview.postMessage({ type: 'spec2rtlResult', result });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            this._view?.webview.postMessage({ type: 'response', text: `Spec2RTL error: ${msg}`, role: 'error' });
        }
    }

    private _postJson(url: string, body: string): Promise<unknown> {
        return new Promise((resolve, reject) => {
            const u = new URL(url);
            const req = http.request({
                hostname: u.hostname,
                port: parseInt(u.port || '8765', 10),
                path: u.pathname,
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
            }, (res) => {
                const chunks: Buffer[] = [];
                res.on('data', (c: Buffer) => chunks.push(c));
                res.on('end', () => {
                    try { resolve(JSON.parse(Buffer.concat(chunks).toString())); }
                    catch (e) { reject(e); }
                });
            });
            req.on('error', reject);
            req.write(body);
            req.end();
        });
    }

    private _streamRequest(url: string, body: string): Promise<void> {
        return new Promise((resolve) => {
            const u = new URL(url);
            const req = http.request({
                hostname: u.hostname,
                port: parseInt(u.port || '8765', 10),
                path: u.pathname,
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
            }, (res) => {
                let buf = '';
                res.on('data', (chunk: Buffer) => {
                    buf += chunk.toString();
                    const lines = buf.split('\n');
                    buf = lines.pop() ?? '';
                    for (const line of lines) {
                        if (!line.startsWith('data: ')) { continue; }
                        const data = line.slice(6).trim();
                        if (data === '[DONE]') {
                            this._view?.webview.postMessage({ type: 'streamEnd' });
                            resolve();
                            return;
                        }
                        try {
                            const p = JSON.parse(data) as { chunk?: string; error?: string };
                            if (p.chunk) {
                                this._view?.webview.postMessage({ type: 'streamChunk', chunk: p.chunk });
                            } else if (p.error) {
                                this._view?.webview.postMessage({ type: 'response', text: `Error: ${p.error}`, role: 'error' });
                            }
                        } catch { /* ignore malformed SSE */ }
                    }
                });
                res.on('end', resolve);
            });

            req.on('error', (err) => {
                const routerUrl = vscode.workspace.getConfiguration('vibe4fpga')
                    .get<string>('llmRouter.url', 'http://localhost:8765');
                this._view?.webview.postMessage({
                    type: 'response',
                    role: 'error',
                    text: `无法连接到 LLM Router (${routerUrl})\n\n${err.message}\n\n请先运行：make dev-router`,
                });
                resolve();
            });

            req.write(body);
            req.end();
        });
    }

    // ── HTML ─────────────────────────────────────────────────────────────────

    private _html(webview: vscode.Webview): string {
        const n = nonce();
        const mcpDefs = JSON.stringify(MCP_SERVICE_DEFS.map(d => ({ id: d.id, label: d.label })));

        return /* html */`<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${n}';">
<title>FPGA Vibe Coding</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{
    font-family:var(--vscode-font-family);
    font-size:var(--vscode-font-size);
    color:var(--vscode-foreground);
    background:var(--vscode-editor-background);
    height:100vh;display:flex;flex-direction:column;overflow:hidden
  }

  /* ── Settings panel ── */
  #settings{border-bottom:1px solid var(--vscode-panel-border)}
  #settings summary{
    padding:6px 10px;cursor:pointer;user-select:none;
    font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
    background:var(--vscode-sideBarSectionHeader-background);
    list-style:none;display:flex;align-items:center;gap:6px
  }
  #settings summary::before{content:'▸';font-size:9px;transition:transform .15s}
  #settings[open] summary::before{transform:rotate(90deg)}
  .settings-body{
    padding:10px;display:flex;flex-direction:column;gap:8px;
    max-height:55vh;overflow-y:auto;
    background:var(--vscode-editor-background)
  }
  .cfg-label{font-size:11px;color:var(--vscode-descriptionForeground);margin-bottom:2px}
  .cfg-group{display:flex;flex-direction:column;gap:2px}
  select,input[type=text],input[type=password]{
    background:var(--vscode-input-background);
    color:var(--vscode-input-foreground);
    border:1px solid var(--vscode-input-border,#555);
    padding:4px 6px;border-radius:2px;width:100%;font-size:12px;
    font-family:inherit
  }
  .btn-save{
    padding:4px 10px;background:var(--vscode-button-background);
    color:var(--vscode-button-foreground);border:none;border-radius:2px;
    cursor:pointer;font-size:12px;align-self:flex-start;margin-top:2px
  }
  .btn-save:hover{background:var(--vscode-button-hoverBackground)}
  .cfg-divider{border:none;border-top:1px solid var(--vscode-panel-border);margin:4px 0}
  .mcp-row{
    display:flex;align-items:center;gap:6px;padding:3px 0;font-size:12px
  }
  .dot{
    width:8px;height:8px;border-radius:50%;flex-shrink:0;
    background:var(--vscode-descriptionForeground)
  }
  .dot.stopped {background:#606060}
  .dot.starting{background:#d4ac0d}
  .dot.running {background:#4caf50}
  .dot.error   {background:#f44336}
  .mcp-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mcp-btn{
    padding:2px 8px;font-size:11px;
    background:var(--vscode-button-secondaryBackground,#3a3d41);
    color:var(--vscode-button-secondaryForeground,#ccc);
    border:none;border-radius:2px;cursor:pointer;flex-shrink:0
  }
  .mcp-btn:hover{background:var(--vscode-button-secondaryHoverBackground,#4a4d51)}
  .cfg-saved{font-size:11px;color:#4caf50;display:none}

  /* ── Messages ── */
  #messages{
    flex:1;overflow-y:auto;padding:10px;
    display:flex;flex-direction:column;gap:8px
  }
  .message{
    padding:7px 10px;border-radius:4px;max-width:95%;
    white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.55
  }
  .user     {background:var(--vscode-inputValidation-infoBackground);align-self:flex-end}
  .assistant{background:var(--vscode-editor-inactiveSelectionBackground);align-self:flex-start}
  .error    {background:var(--vscode-inputValidation-errorBackground);align-self:flex-start}
  .streaming{
    background:var(--vscode-editor-inactiveSelectionBackground);align-self:flex-start;
    border-left:2px solid var(--vscode-progressBar-background)
  }
  .rtl-block{font-family:var(--vscode-editor-font-family,monospace);font-size:11px}
  .placeholder{
    color:var(--vscode-descriptionForeground);font-style:italic;
    text-align:center;margin-top:32px;line-height:1.8;font-size:12px
  }

  /* ── Toolbar ── */
  #toolbar{
    display:flex;gap:4px;padding:4px 8px;
    border-top:1px solid var(--vscode-panel-border)
  }
  #status-bar{
    padding:2px 8px;font-size:11px;
    color:var(--vscode-descriptionForeground);min-height:18px
  }
  .tool-btn{
    padding:3px 8px;font-size:11px;
    background:var(--vscode-button-secondaryBackground,#3a3d41);
    color:var(--vscode-button-secondaryForeground,#ccc);
    border:none;border-radius:2px;cursor:pointer
  }
  .tool-btn:hover{background:var(--vscode-button-secondaryHoverBackground,#4a4d51)}

  /* ── Input ── */
  #input-area{
    display:flex;gap:6px;padding:6px 8px;
    border-top:1px solid var(--vscode-panel-border)
  }
  #message-input{
    flex:1;resize:none;padding:5px 7px;
    background:var(--vscode-input-background);
    color:var(--vscode-input-foreground);
    border:1px solid var(--vscode-input-border,#555);
    border-radius:2px;font-family:inherit;font-size:12px;height:54px
  }
  #send-btn{
    padding:5px 12px;
    background:var(--vscode-button-background);
    color:var(--vscode-button-foreground);
    border:none;border-radius:2px;cursor:pointer;align-self:flex-end;font-size:12px
  }
  #send-btn:hover{background:var(--vscode-button-hoverBackground)}
  #send-btn:disabled{opacity:.5;cursor:not-allowed}
</style>
</head>
<body>

<!-- ── Settings panel ── -->
<details id="settings">
  <summary>⚙ 配置 &amp; 服务</summary>
  <div class="settings-body">

    <div class="cfg-group">
      <div class="cfg-label">LLM Backend</div>
      <select id="cfg-backend">
        <option value="claude">Claude Sonnet（推荐）</option>
        <option value="claude-opus">Claude Opus</option>
        <option value="claude-haiku">Claude Haiku（快速）</option>
        <option value="ollama">Ollama（本地）</option>
        <option value="rtlcoder">RTLCoder（HDL 微调）</option>
        <option value="codev">CodeV（HDL 微调）</option>
      </select>
    </div>

    <div class="cfg-group">
      <div class="cfg-label">Router URL</div>
      <input id="cfg-url" type="text" placeholder="http://localhost:8765">
    </div>

    <div class="cfg-group">
      <div class="cfg-label">API Key</div>
      <input id="cfg-key" type="password" placeholder="sk-ant-…（留空保持不变）">
    </div>

    <div style="display:flex;align-items:center;gap:8px">
      <button class="btn-save" onclick="saveConfig()">保存配置</button>
      <span class="cfg-saved" id="cfg-saved">✓ 已保存</span>
    </div>

    <hr class="cfg-divider">
    <div class="cfg-label">MCP 服务（可选）</div>
    <div id="mcp-list"></div>

  </div>
</details>

<!-- ── Messages ── -->
<div id="messages">
  <div class="placeholder">
    用自然语言描述你的 FPGA 设计需求<br>
    <small>例：设计一个带异步复位的 8-bit 计数器</small><br>
    <small>或点击 ⚡ Spec→RTL 直接生成 RTL</small>
  </div>
</div>

<div id="status-bar"></div>

<!-- ── Toolbar ── -->
<div id="toolbar">
  <button class="tool-btn" onclick="triggerSpec2RTL()" title="将输入框内容作为规格生成 RTL">⚡ Spec→RTL</button>
  <button class="tool-btn" onclick="clearChat()" title="清空对话">✕ 清空</button>
</div>

<!-- ── Input ── -->
<div id="input-area">
  <textarea id="message-input" placeholder="描述设计意图… (Ctrl+Enter 发送)"></textarea>
  <button id="send-btn" onclick="sendMessage()">发送</button>
</div>

<script nonce="${n}">
const vscode      = acquireVsCodeApi();
const messagesEl  = document.getElementById('messages');
const sendBtn     = document.getElementById('send-btn');
const statusBar   = document.getElementById('status-bar');
const MCP_SERVICES = ${mcpDefs};

let firstMessage = true;
let streamingDiv  = null;

// ── Render MCP list ──────────────────────────────────────────────────────────
(function renderMcpList() {
  const list = document.getElementById('mcp-list');
  for (const svc of MCP_SERVICES) {
    const row = document.createElement('div');
    row.className = 'mcp-row';
    row.innerHTML =
      '<span class="dot stopped" id="dot-' + svc.id + '"></span>' +
      '<span class="mcp-name">' + svc.label + '</span>' +
      '<button class="mcp-btn" id="btn-' + svc.id + '" ' +
        'onclick="toggleMcp(\\'' + svc.id + '\\')">Start</button>';
    list.appendChild(row);
  }
})();

// ── MCP helpers ──────────────────────────────────────────────────────────────
function toggleMcp(id) {
  vscode.postMessage({ type: 'toggleMcp', id });
}

function applyMcpStatus(id, status) {
  const dot = document.getElementById('dot-' + id);
  const btn = document.getElementById('btn-' + id);
  if (!dot || !btn) { return; }
  dot.className = 'dot ' + status;
  const running = status === 'running' || status === 'starting';
  btn.textContent = running ? 'Stop' : 'Start';
  btn.style.color = running ? 'var(--vscode-errorForeground,#f88)' : '';
}

// ── Config helpers ───────────────────────────────────────────────────────────
function saveConfig() {
  vscode.postMessage({
    type: 'saveConfig',
    config: {
      backend:   document.getElementById('cfg-backend').value,
      routerUrl: document.getElementById('cfg-url').value,
      apiKey:    document.getElementById('cfg-key').value,
    },
  });
}

// ── Chat helpers ─────────────────────────────────────────────────────────────
function setStatus(text) { statusBar.textContent = text; }

function clearChat() {
  messagesEl.innerHTML =
    '<div class="placeholder">用自然语言描述你的 FPGA 设计需求…</div>';
  firstMessage = true;
  streamingDiv = null;
}

function addMessage(text, role) {
  if (firstMessage) { messagesEl.innerHTML = ''; firstMessage = false; }
  const div = document.createElement('div');
  div.className = 'message ' + role;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function sendMessage() {
  const input = document.getElementById('message-input');
  const text  = input.value.trim();
  if (!text) { return; }
  addMessage(text, 'user');
  vscode.postMessage({ type: 'sendMessage', text });
  input.value = '';
  sendBtn.disabled = true;
  setStatus('思考中…');
}

function triggerSpec2RTL() {
  const input = document.getElementById('message-input');
  const spec  = input.value.trim();
  if (!spec) { alert('请先在输入框中描述设计需求'); return; }
  addMessage(spec, 'user');
  input.value = '';
  sendBtn.disabled = true;
  setStatus('运行 Spec2RTL…');
  vscode.postMessage({ type: 'spec2rtl', spec });
}

document.getElementById('message-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { sendMessage(); }
});

// ── Message handler ──────────────────────────────────────────────────────────
window.addEventListener('message', (event) => {
  const msg = event.data;

  if (msg.type === 'init') {
    document.getElementById('cfg-backend').value = msg.config.backend;
    document.getElementById('cfg-url').value     = msg.config.routerUrl;
    // Don't pre-fill API key for security
    for (const [id, status] of Object.entries(msg.mcpStatuses)) {
      applyMcpStatus(id, status);
    }
    return;
  }

  if (msg.type === 'mcpStatus') {
    applyMcpStatus(msg.id, msg.status);
    return;
  }

  if (msg.type === 'configSaved') {
    const el = document.getElementById('cfg-saved');
    el.style.display = 'inline';
    setTimeout(() => { el.style.display = 'none'; }, 2000);
    return;
  }

  if (msg.type === 'streamChunk') {
    if (!streamingDiv) {
      if (firstMessage) { messagesEl.innerHTML = ''; firstMessage = false; }
      streamingDiv = document.createElement('div');
      streamingDiv.className = 'message streaming';
      messagesEl.appendChild(streamingDiv);
    }
    streamingDiv.textContent += msg.chunk;
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return;
  }

  if (msg.type === 'streamEnd') {
    if (streamingDiv) {
      streamingDiv.classList.remove('streaming');
      streamingDiv.classList.add('assistant');
      streamingDiv = null;
    }
    sendBtn.disabled = false;
    setStatus('');
    return;
  }

  if (msg.type === 'response') {
    addMessage(msg.text, msg.role || 'assistant');
    sendBtn.disabled = false;
    setStatus('');
    return;
  }

  if (msg.type === 'statusUpdate') {
    setStatus(msg.text);
    return;
  }

  if (msg.type === 'spec2rtlResult') {
    const r = msg.result;
    let summary = '=== Spec2RTL Result ===\\n';
    summary += 'Module: ' + r.module_name + '  Score: ' + r.score.toFixed(0) + '/100\\n';
    summary += 'Status: ' + (r.passed ? '✓ PASS' : '✗ FAIL') + '\\n';
    if (r.declared_decisions?.length) {
      summary += '\\nAutonomous decisions:\\n';
      r.declared_decisions.forEach(d => { summary += '  • ' + d + '\\n'; });
    }
    summary += '\\n--- RTL Code ---\\n' + r.rtl_code;
    if (firstMessage) { messagesEl.innerHTML = ''; firstMessage = false; }
    const div = document.createElement('div');
    div.className = 'message assistant rtl-block';
    div.textContent = summary;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    sendBtn.disabled = false;
    setStatus('');
    return;
  }

  if (msg.type === 'prefill') {
    document.getElementById('message-input').value = msg.text;
  }
});

// Signal that the webview is ready
vscode.postMessage({ type: 'ready' });
</script>
</body>
</html>`;
    }
}
