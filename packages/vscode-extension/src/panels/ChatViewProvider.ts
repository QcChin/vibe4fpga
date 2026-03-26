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

    // ── HTML (Claude Code–inspired layout) ───────────────────────────────────

    private _html(_webview: vscode.Webview): string {
        const n       = nonce();
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
:root {
  --accent:      #c96442;
  --accent-h:    #e07050;
  --accent-dim:  rgba(201,100,66,.15);
  --bg:          var(--vscode-editor-background, #1e1e1e);
  --surface:     var(--vscode-input-background,  #2a2a2a);
  --border:      var(--vscode-panel-border,      rgba(255,255,255,.1));
  --text:        var(--vscode-foreground,        #e0e0e0);
  --muted:       var(--vscode-descriptionForeground, #888);
  --font:        var(--vscode-font-family, -apple-system, sans-serif);
  --mono:        var(--vscode-editor-font-family, monospace);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);font-size:13px;color:var(--text);background:var(--bg);
     height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ── Header ── */
#hdr{display:flex;align-items:center;padding:7px 10px;
     border-bottom:1px solid var(--border);flex-shrink:0;gap:6px}
.logo{flex:1;font-size:12px;font-weight:600;letter-spacing:.04em;
      display:flex;align-items:center;gap:6px}
.logo-icon{color:var(--accent);flex-shrink:0}
.hdr-btns{display:flex;gap:2px}
.ibtn{background:none;border:none;color:var(--muted);cursor:pointer;
      padding:4px;border-radius:4px;display:flex;align-items:center;line-height:1;
      transition:color .15s,background .15s}
.ibtn:hover{color:var(--text);background:rgba(255,255,255,.08)}
.ibtn.on{color:var(--accent)}

/* ── Settings drawer ── */
#cfg-drawer{overflow:hidden;max-height:0;flex-shrink:0;
            transition:max-height .25s ease-out,border-bottom-width 0s .25s}
#cfg-drawer.open{max-height:540px;border-bottom:1px solid var(--border);
                 transition:max-height .3s ease-in}
.cfg-body{padding:12px;display:flex;flex-direction:column;gap:10px;
          max-height:540px;overflow-y:auto}
.cfg-sec{display:flex;flex-direction:column;gap:6px}
.cfg-ttl{font-size:10px;font-weight:700;text-transform:uppercase;
         letter-spacing:.07em;color:var(--muted)}
.fld{display:flex;flex-direction:column;gap:3px}
.fld label{font-size:11px;color:var(--muted)}
.fld select,.fld input{background:var(--surface);color:var(--text);
  border:1px solid var(--border);border-radius:4px;padding:5px 8px;
  font-size:12px;width:100%;font-family:var(--font);outline:none}
.fld select:focus,.fld input:focus{border-color:var(--accent)}
.btn-save{background:var(--accent);color:#fff;border:none;border-radius:4px;
          padding:5px 14px;font-size:12px;cursor:pointer;align-self:flex-start;
          font-family:var(--font)}
.btn-save:hover{background:var(--accent-h)}
.cfg-ok{font-size:11px;color:#4caf50;display:none;margin-left:6px}
.sep{border:none;border-top:1px solid var(--border);margin:2px 0}
/* MCP rows */
.mcp-row{display:flex;align-items:center;gap:7px;padding:3px 0;font-size:12px}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.dot.stopped {background:#555}
.dot.starting{background:#d4ac0d}
.dot.running {background:#4caf50}
.dot.error   {background:#f44336}
.mcp-name{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mcp-btn{font-size:11px;padding:2px 8px;background:rgba(255,255,255,.06);
         color:var(--muted);border:1px solid var(--border);border-radius:3px;
         cursor:pointer;flex-shrink:0;font-family:var(--font)}
.mcp-btn:hover{color:var(--text);background:rgba(255,255,255,.11)}
.mcp-btn.stop{color:#f98;border-color:rgba(244,100,80,.35)}

/* ── Messages ── */
#msgs{flex:1;overflow-y:auto;display:flex;flex-direction:column}

/* Empty state */
#empty{flex:1;display:flex;flex-direction:column;align-items:center;
       justify-content:center;gap:14px;padding:28px 18px;text-align:center}
#empty p{font-size:13px;line-height:1.65;color:var(--text)}
#empty small{font-size:11px;color:var(--muted);line-height:1.6}

/* Message list */
.msg-list{display:flex;flex-direction:column;padding:10px 0;flex:1}
.mg{padding:6px 12px}
.mg-role{font-size:10px;font-weight:700;letter-spacing:.05em;margin-bottom:3px}
.mg-role.you{color:var(--accent);text-align:right}
.mg-role.ai {color:var(--muted)}
.mg-body{font-size:12px;line-height:1.65;white-space:pre-wrap;word-break:break-word}
.mg-body.you{text-align:right}
.mg-body.err{color:#f98}
.mg-body.streaming{padding-left:8px;border-left:2px solid var(--accent)}
.mg-code{font-family:var(--mono);font-size:11px;background:rgba(0,0,0,.28);
         border:1px solid var(--border);border-radius:5px;padding:8px 10px;
         margin-top:6px;overflow-x:auto;white-space:pre;line-height:1.5}

/* ── Status bar ── */
#sbar{height:18px;padding:0 10px;font-size:11px;color:var(--accent);
      display:flex;align-items:center;gap:5px;flex-shrink:0}
.spin{width:10px;height:10px;border:1.5px solid var(--accent);
      border-top-color:transparent;border-radius:50%;
      animation:rot .7s linear infinite;display:none}
@keyframes rot{to{transform:rotate(360deg)}}

/* ── Input area ── */
#inp-wrap{padding:8px 10px 10px;border-top:1px solid var(--border);flex-shrink:0}
#inp-box{display:flex;align-items:flex-end;gap:6px;background:var(--surface);
         border:1px solid var(--border);border-radius:8px;
         padding:8px 8px 8px 12px;transition:border-color .15s}
#inp-box:focus-within{border-color:rgba(201,100,66,.5)}
#msg-inp{flex:1;background:none;border:none;outline:none;color:var(--text);
         font-family:var(--font);font-size:13px;line-height:1.5;
         resize:none;min-height:20px;max-height:110px;overflow-y:auto}
#msg-inp::placeholder{color:var(--muted)}
#send{width:28px;height:28px;background:var(--accent);color:#fff;border:none;
      border-radius:6px;cursor:pointer;display:flex;align-items:center;
      justify-content:center;flex-shrink:0;transition:background .15s}
#send:hover{background:var(--accent-h)}
#send:disabled{background:var(--muted);opacity:.5;cursor:not-allowed}
#act-bar{display:flex;align-items:center;gap:4px;padding-top:6px}
.abtn{background:none;border:1px solid var(--border);border-radius:4px;
      color:var(--muted);cursor:pointer;padding:3px 8px;font-size:11px;
      font-family:var(--font);display:flex;align-items:center;gap:4px;
      transition:color .15s,border-color .15s,background .15s}
.abtn:hover{color:var(--text);border-color:rgba(255,255,255,.2);
            background:rgba(255,255,255,.05)}
.abtn.hi{color:var(--accent);border-color:rgba(201,100,66,.4)}
.spacer{flex:1}

/* Scrollbar */
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
::-webkit-scrollbar-thumb:hover{background:var(--muted)}
</style>
</head>
<body>

<!-- Header -->
<div id="hdr">
  <div class="logo">
    <svg class="logo-icon" width="15" height="15" viewBox="0 0 16 16" fill="currentColor">
      <rect x="4" y="4" width="8" height="8" rx="1.2"/>
      <rect x="1.5" y="5.5" width="2.5" height="1.5" rx=".4"/>
      <rect x="1.5" y="9"   width="2.5" height="1.5" rx=".4"/>
      <rect x="12"  y="5.5" width="2.5" height="1.5" rx=".4"/>
      <rect x="12"  y="9"   width="2.5" height="1.5" rx=".4"/>
      <rect x="5.5" y="1.5" width="1.5" height="2.5" rx=".4"/>
      <rect x="9"   y="1.5" width="1.5" height="2.5" rx=".4"/>
      <rect x="5.5" y="12"  width="1.5" height="2.5" rx=".4"/>
      <rect x="9"   y="12"  width="1.5" height="2.5" rx=".4"/>
    </svg>
    FPGA Vibe Coding
  </div>
  <div class="hdr-btns">
    <!-- Settings gear -->
    <button class="ibtn" id="cfg-btn" onclick="toggleCfg()" title="配置">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
        <path d="M9.405 1.05c-.413-1.4-2.397-1.4-2.81 0l-.1.34a1.464 1.464 0 0
          1-2.105.872l-.31-.17c-1.283-.698-2.686.705-1.987 1.987l.169.311c.446.82.023
          1.841-.872 2.105l-.34.1c-1.4.413-1.4 2.397 0 2.81l.34.1a1.464 1.464 0 0
          1 .872 2.105l-.17.31c-.698 1.283.705 2.686 1.987 1.987l.311-.169a1.464
          1.464 0 0 1 2.105.872l.1.34c.413 1.4 2.397 1.4 2.81
          0l.1-.34a1.464 1.464 0 0 1 2.105-.872l.31.17c1.283.698
          2.686-.705 1.987-1.987l-.169-.311a1.464 1.464 0 0
          1 .872-2.105l.34-.1c1.4-.413 1.4-2.397 0-2.81l-.34-.1a1.464
          1.464 0 0 1-.872-2.105l.17-.31c.698-1.283-.705-2.686-1.987-1.987l-.311.169a1.464
          1.464 0 0 1-2.105-.872l-.1-.34zM8 10.93a2.929 2.929 0 1 1 0-5.86
          2.929 2.929 0 0 1 0 5.858z"/>
      </svg>
    </button>
    <!-- Clear -->
    <button class="ibtn" onclick="clearChat()" title="清空对话">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
        <path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm2.5
          0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm3 .5a.5.5 0 0 0-1
          0v6a.5.5 0 0 0 1 0V6z"/>
        <path fill-rule="evenodd" d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2
          0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1
          1 1h3.5a1 1 0 0 1 1 1v1zM4.118 4L4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0
          1-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z"/>
      </svg>
    </button>
  </div>
</div>

<!-- Settings drawer -->
<div id="cfg-drawer">
  <div class="cfg-body">

    <div class="cfg-sec">
      <div class="cfg-ttl">LLM 配置</div>
      <div class="fld">
        <label>Backend</label>
        <select id="cfg-backend">
          <option value="claude">Claude Sonnet（默认）</option>
          <option value="claude-opus">Claude Opus</option>
          <option value="claude-haiku">Claude Haiku（快速）</option>
          <option value="ollama">Ollama（本地）</option>
          <option value="rtlcoder">RTLCoder（HDL 微调）</option>
          <option value="codev">CodeV（HDL 微调）</option>
        </select>
      </div>
      <div class="fld">
        <label>Router URL</label>
        <input id="cfg-url" type="text" placeholder="http://localhost:8765">
      </div>
      <div class="fld">
        <label>API Key</label>
        <input id="cfg-key" type="password" placeholder="sk-ant-…（留空保持不变）">
      </div>
      <div style="display:flex;align-items:center">
        <button class="btn-save" onclick="saveCfg()">保存</button>
        <span class="cfg-ok" id="cfg-ok">✓ 已保存</span>
      </div>
    </div>

    <hr class="sep">

    <div class="cfg-sec">
      <div class="cfg-ttl">MCP 服务</div>
      <div id="mcp-list"></div>
    </div>

  </div>
</div>

<!-- Messages -->
<div id="msgs">
  <div id="empty">
    <!-- FPGA chip illustration -->
    <svg width="54" height="54" viewBox="0 0 56 56" fill="none">
      <rect x="14" y="14" width="28" height="28" rx="3.5" fill="#c96442"/>
      <rect x="19" y="8"  width="4" height="6" rx="1" fill="#c96442"/>
      <rect x="26" y="8"  width="4" height="6" rx="1" fill="#c96442"/>
      <rect x="33" y="8"  width="4" height="6" rx="1" fill="#c96442"/>
      <rect x="19" y="42" width="4" height="6" rx="1" fill="#c96442"/>
      <rect x="26" y="42" width="4" height="6" rx="1" fill="#c96442"/>
      <rect x="33" y="42" width="4" height="6" rx="1" fill="#c96442"/>
      <rect x="8"  y="19" width="6" height="4" rx="1" fill="#c96442"/>
      <rect x="8"  y="26" width="6" height="4" rx="1" fill="#c96442"/>
      <rect x="8"  y="33" width="6" height="4" rx="1" fill="#c96442"/>
      <rect x="42" y="19" width="6" height="4" rx="1" fill="#c96442"/>
      <rect x="42" y="26" width="6" height="4" rx="1" fill="#c96442"/>
      <rect x="42" y="33" width="6" height="4" rx="1" fill="#c96442"/>
      <rect x="18" y="18" width="8" height="8" rx="1.5" fill="rgba(0,0,0,.28)"/>
      <rect x="30" y="18" width="8" height="8" rx="1.5" fill="rgba(0,0,0,.28)"/>
      <rect x="18" y="30" width="8" height="8" rx="1.5" fill="rgba(0,0,0,.28)"/>
      <rect x="30" y="30" width="8" height="8" rx="1.5" fill="rgba(0,0,0,.28)"/>
      <circle cx="28" cy="28" r="3" fill="rgba(255,255,255,.3)"/>
    </svg>
    <p>描述你的 FPGA 设计需求<br>或询问代码库中的任何问题</p>
    <small>例：设计一个带异步复位的 8-bit 计数器<br>或：解释这段 Verilog 里的时钟域交叉问题</small>
  </div>
</div>

<!-- Status bar -->
<div id="sbar">
  <div class="spin" id="spin"></div>
  <span id="stext"></span>
</div>

<!-- Input -->
<div id="inp-wrap">
  <div id="inp-box">
    <textarea id="msg-inp" rows="1"
              placeholder="描述 FPGA 设计需求… (Ctrl+Enter 发送)"></textarea>
    <button id="send" onclick="send()" title="发送">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 15a.5.5 0 0 0 .5-.5V2.707l3.146 3.147a.5.5 0 0 0
          .708-.708l-4-4a.5.5 0 0 0-.708 0l-4 4a.5.5 0 1 0 .708.708L7.5
          2.707V14.5a.5.5 0 0 0 .5.5z"/>
      </svg>
    </button>
  </div>
  <div id="act-bar">
    <button class="abtn" onclick="specToRtl()" title="将输入框内容生成 RTL">
      <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor">
        <path d="M11.251.068a.5.5 0 0 1 .227.58L9.677 6.5H13a.5.5 0 0 1
          .364.843l-8 8.5a.5.5 0 0 1-.842-.49L6.323 9.5H3a.5.5 0 0 1-.364-.843l8-8.5a.5.5
          0 0 1 .615-.09z"/>
      </svg>
      Spec→RTL
    </button>
    <div class="spacer"></div>
    <button class="abtn hi" id="ask-btn">Ask before edits</button>
  </div>
</div>

<script nonce="${n}">
const vscode    = acquireVsCodeApi();
const msgsEl    = document.getElementById('msgs');
const emptyEl   = document.getElementById('empty');
const sendBtn   = document.getElementById('send');
const spinEl    = document.getElementById('spin');
const stextEl   = document.getElementById('stext');
const cfgDrawer = document.getElementById('cfg-drawer');
const cfgBtn    = document.getElementById('cfg-btn');
const MCP       = ${mcpDefs};

let hasMsgs    = false;
let msgList    = null;
let streaming  = null;
let cfgOpen    = false;

// ── Settings ─────────────────────────────────────────────────────────────────
function toggleCfg() {
  cfgOpen = !cfgOpen;
  cfgDrawer.classList.toggle('open', cfgOpen);
  cfgBtn.classList.toggle('on', cfgOpen);
}
function saveCfg() {
  vscode.postMessage({ type:'saveConfig', config:{
    backend:   document.getElementById('cfg-backend').value,
    routerUrl: document.getElementById('cfg-url').value,
    apiKey:    document.getElementById('cfg-key').value,
  }});
}

// ── MCP list ─────────────────────────────────────────────────────────────────
(function() {
  const list = document.getElementById('mcp-list');
  for (const s of MCP) {
    const row = document.createElement('div');
    row.className = 'mcp-row';
    row.innerHTML =
      '<span class="dot stopped" id="d-' + s.id + '"></span>' +
      '<span class="mcp-name">' + s.label + '</span>' +
      '<button class="mcp-btn" id="b-' + s.id + '" ' +
        'onclick="tMcp(\\'' + s.id + '\\')">Start</button>';
    list.appendChild(row);
  }
})();
function tMcp(id) { vscode.postMessage({ type:'toggleMcp', id }); }
function applyMcp(id, st) {
  const d = document.getElementById('d-' + id);
  const b = document.getElementById('b-' + id);
  if (!d || !b) return;
  d.className = 'dot ' + st;
  const on = st === 'running' || st === 'starting';
  b.textContent = on ? 'Stop' : 'Start';
  b.classList.toggle('stop', on);
}

// ── Status ───────────────────────────────────────────────────────────────────
function setSt(txt) {
  stextEl.textContent = txt;
  spinEl.style.display = txt ? 'block' : 'none';
}

// ── Chat helpers ─────────────────────────────────────────────────────────────
function clearChat() {
  msgsEl.innerHTML = '';
  msgsEl.appendChild(emptyEl);
  emptyEl.style.display = 'flex';
  hasMsgs = false; msgList = null; streaming = null;
  sendBtn.disabled = false; setSt('');
}
function getList() {
  if (!hasMsgs) {
    emptyEl.style.display = 'none';
    msgList = document.createElement('div');
    msgList.className = 'msg-list';
    msgsEl.appendChild(msgList);
    hasMsgs = true;
  }
  return msgList;
}
function addMsg(text, role) {
  const list = getList();
  const g = document.createElement('div'); g.className = 'mg';
  const r = document.createElement('div');
  r.className = 'mg-role ' + (role === 'you' ? 'you' : 'ai');
  r.textContent = role === 'you' ? 'You' : 'FPGA Vibe';
  const b = document.createElement('div');
  b.className = 'mg-body ' + role;
  b.textContent = text;
  g.appendChild(r); g.appendChild(b);
  list.appendChild(g);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return b;
}

function send() {
  const ta = document.getElementById('msg-inp');
  const t  = ta.value.trim(); if (!t) return;
  addMsg(t, 'you');
  vscode.postMessage({ type:'sendMessage', text:t });
  ta.value = ''; resize(ta);
  sendBtn.disabled = true; setSt('思考中…');
}
function specToRtl() {
  const ta = document.getElementById('msg-inp');
  const sp = ta.value.trim();
  if (!sp) { alert('请先在输入框中描述设计需求'); return; }
  addMsg(sp, 'you');
  ta.value = ''; resize(ta);
  sendBtn.disabled = true; setSt('运行 Spec2RTL…');
  vscode.postMessage({ type:'spec2rtl', spec:sp });
}
function resize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 110) + 'px';
}
document.getElementById('msg-inp').addEventListener('input', function(){ resize(this); });
document.getElementById('msg-inp').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
});

// ── Message handler ───────────────────────────────────────────────────────────
window.addEventListener('message', ev => {
  const m = ev.data;
  switch (m.type) {

    case 'init':
      document.getElementById('cfg-backend').value = m.config.backend;
      document.getElementById('cfg-url').value     = m.config.routerUrl;
      for (const [id, st] of Object.entries(m.mcpStatuses)) applyMcp(id, st);
      break;

    case 'mcpStatus':  applyMcp(m.id, m.status); break;

    case 'configSaved': {
      const ok = document.getElementById('cfg-ok');
      ok.style.display = 'inline';
      setTimeout(() => { ok.style.display = 'none'; }, 2000);
      break;
    }

    case 'streamChunk':
      if (!streaming) {
        const list = getList();
        const g = document.createElement('div'); g.className = 'mg';
        const r = document.createElement('div'); r.className = 'mg-role ai'; r.textContent = 'FPGA Vibe';
        streaming = document.createElement('div'); streaming.className = 'mg-body streaming';
        g.appendChild(r); g.appendChild(streaming); list.appendChild(g);
      }
      streaming.textContent += m.chunk;
      msgsEl.scrollTop = msgsEl.scrollHeight;
      break;

    case 'streamEnd':
      if (streaming) { streaming.classList.remove('streaming'); streaming = null; }
      sendBtn.disabled = false; setSt(''); break;

    case 'response':
      addMsg(m.text, m.role === 'error' ? 'err' : 'ai');
      sendBtn.disabled = false; setSt(''); break;

    case 'statusUpdate': setSt(m.text); break;

    case 'spec2rtlResult': {
      const rv = m.result;
      const list = getList();
      const g = document.createElement('div'); g.className = 'mg';
      const r = document.createElement('div'); r.className = 'mg-role ai'; r.textContent = 'FPGA Vibe';
      const sum = document.createElement('div'); sum.className = 'mg-body';
      sum.textContent = 'Module: ' + rv.module_name +
        '  Score: ' + rv.score.toFixed(0) + '/100  ' + (rv.passed ? '✓ PASS' : '✗ FAIL');
      const code = document.createElement('div'); code.className = 'mg-code';
      code.textContent = rv.rtl_code;
      g.appendChild(r); g.appendChild(sum); g.appendChild(code);
      list.appendChild(g);
      msgsEl.scrollTop = msgsEl.scrollHeight;
      sendBtn.disabled = false; setSt(''); break;
    }

    case 'prefill': {
      const ta = document.getElementById('msg-inp');
      ta.value = m.text; resize(ta); ta.focus(); break;
    }
  }
});

vscode.postMessage({ type:'ready' });
</script>
</body>
</html>`;
    }
}
