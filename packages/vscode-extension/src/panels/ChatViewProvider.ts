import * as http from 'http';
import * as vscode from 'vscode';
import { McpManager, MCP_SERVICE_DEFS, McpStatus } from '../services/McpManager';

function nonce(): string {
    let s = '';
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) { s += chars[Math.floor(Math.random() * chars.length)]; }
    return s;
}

type Attachment =
    | { kind: 'image';  name: string; mimeType: string; base64: string }
    | { kind: 'text';   name: string; mimeType: string; content: string }
    | { kind: 'binary'; name: string; mimeType: string; base64: string };

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
                    await this._handleChat(
                        msg.text as string,
                        msg.attachments as Attachment[] | undefined,
                    );
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

    private async _handleChat(
        text: string,
        attachments?: Attachment[],
    ): Promise<void> {
        const cfg       = vscode.workspace.getConfiguration('vibe4fpga');
        const routerUrl = cfg.get<string>('llmRouter.url', 'http://localhost:8765');
        const model     = cfg.get<string>('llm.backend',   'claude');

        type ContentPart =
            | { type: 'text'; text: string }
            | { type: 'image'; source: { type: 'base64'; media_type: string; data: string } };

        let content: string | ContentPart[];

        if (attachments && attachments.length > 0) {
            const parts: ContentPart[] = [];
            for (const att of attachments) {
                if (att.kind === 'image') {
                    parts.push({
                        type: 'image',
                        source: { type: 'base64', media_type: att.mimeType, data: att.base64 },
                    });
                } else if (att.kind === 'text') {
                    const ext = att.name.split('.').pop() ?? '';
                    parts.push({
                        type: 'text',
                        text: `\`\`\`${ext}\n// File: ${att.name}\n${att.content}\n\`\`\``,
                    });
                } else {
                    // binary: include as a note only
                    parts.push({ type: 'text', text: `[Binary file attached: ${att.name} (${att.mimeType})]` });
                }
            }
            if (text) { parts.push({ type: 'text', text }); }
            content = parts;
        } else {
            content = text;
        }

        const body = JSON.stringify({
            messages: [{ role: 'user', content }],
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
      content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${n}'; img-src data: blob:;">
<title>FPGA Vibe Coding</title>
<style>
:root {
  --accent:     #c96442;
  --accent-h:   #e07050;
  --bg:         var(--vscode-editor-background,      #1e1e1e);
  --surface:    var(--vscode-input-background,       #252526);
  --surface2:   var(--vscode-sideBar-background,     #1e1e1e);
  --border:     var(--vscode-panel-border,           rgba(255,255,255,.08));
  --text:       var(--vscode-foreground,             #cccccc);
  --muted:      var(--vscode-descriptionForeground,  #6e6e6e);
  --user-bg:    rgba(201,100,66,.12);
  --code-bg:    rgba(0,0,0,.3);
  --font:       var(--vscode-font-family,            -apple-system, BlinkMacSystemFont, sans-serif);
  --mono:       var(--vscode-editor-font-family,     'SF Mono', Consolas, monospace);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);font-size:13px;color:var(--text);background:var(--bg);
     height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ── Header ── */
#hdr{display:flex;align-items:center;padding:6px 8px 6px 10px;gap:5px;flex-shrink:0}
.logo{flex:1;display:flex;align-items:center;gap:6px;min-width:0}
.logo-ic{color:var(--accent);flex-shrink:0}
.logo-name{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;
           text-overflow:ellipsis;letter-spacing:.02em}
#model-badge{font-size:10px;color:var(--muted);background:rgba(255,255,255,.06);
             border:1px solid var(--border);border-radius:3px;
             padding:1px 5px;white-space:nowrap;flex-shrink:0}
.hdr-btns{display:flex;gap:1px;flex-shrink:0}
.ibtn{background:none;border:none;color:var(--muted);cursor:pointer;
      padding:4px 5px;border-radius:4px;display:flex;align-items:center;line-height:1;
      transition:color .15s,background .15s}
.ibtn:hover{color:var(--text);background:rgba(255,255,255,.07)}
.ibtn.on{color:var(--accent)}

/* ── Settings drawer ── */
#cfg-drawer{overflow:hidden;max-height:0;flex-shrink:0;
            transition:max-height .22s ease-out}
#cfg-drawer.open{max-height:560px;border-bottom:1px solid var(--border);
                 transition:max-height .28s ease-in}
.cfg-body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:10px;
          max-height:560px;overflow-y:auto}
.cfg-sec{display:flex;flex-direction:column;gap:5px}
.cfg-ttl{font-size:10px;font-weight:700;text-transform:uppercase;
         letter-spacing:.08em;color:var(--muted);padding-bottom:1px}
.fld{display:flex;flex-direction:column;gap:2px}
.fld label{font-size:11px;color:var(--muted)}
.fld select,.fld input{background:var(--surface);color:var(--text);
  border:1px solid var(--border);border-radius:4px;padding:5px 8px;
  font-size:12px;width:100%;font-family:var(--font);outline:none;
  transition:border-color .15s}
.fld select:focus,.fld input:focus{border-color:rgba(201,100,66,.5)}
.btn-row{display:flex;align-items:center;gap:8px;margin-top:2px}
.btn-save{background:var(--accent);color:#fff;border:none;border-radius:4px;
          padding:5px 14px;font-size:12px;cursor:pointer;font-family:var(--font);
          transition:background .15s}
.btn-save:hover{background:var(--accent-h)}
.cfg-ok{font-size:11px;color:#4caf50;opacity:0;transition:opacity .2s}
.cfg-ok.show{opacity:1}
.sep{border:none;border-top:1px solid var(--border)}
/* MCP rows */
.mcp-row{display:flex;align-items:center;gap:7px;padding:2px 0;font-size:12px}
.dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;transition:background .2s}
.dot.stopped {background:#444}
.dot.starting{background:#c9a62a}
.dot.running {background:#3fb950}
.dot.error   {background:#f85149}
.mcp-name{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
          color:var(--text)}
.mcp-btn{font-size:11px;padding:2px 7px;background:transparent;
         color:var(--muted);border:1px solid var(--border);border-radius:3px;
         cursor:pointer;flex-shrink:0;font-family:var(--font);
         transition:color .15s,background .15s,border-color .15s}
.mcp-btn:hover{color:var(--text);background:rgba(255,255,255,.08)}
.mcp-btn.stop{color:#f98a7a;border-color:rgba(248,81,73,.3)}
.mcp-btn.stop:hover{background:rgba(248,81,73,.1)}

/* ── Messages scroll container ── */
#msgs{flex:1;overflow-y:auto;display:flex;flex-direction:column;
      scroll-behavior:smooth}

/* Empty state */
#empty{flex:1;display:flex;flex-direction:column;align-items:center;
       justify-content:center;gap:16px;padding:24px 16px;text-align:center}
.empty-icon{opacity:.75}
.empty-title{font-size:14px;font-weight:600;color:var(--text);line-height:1.4}
.empty-sub{font-size:12px;color:var(--muted);line-height:1.6;max-width:220px}
.suggestions{display:flex;flex-direction:column;gap:5px;width:100%;max-width:280px}
.sug{background:var(--surface);border:1px solid var(--border);border-radius:6px;
     padding:7px 10px;font-size:11px;color:var(--muted);cursor:pointer;text-align:left;
     font-family:var(--font);line-height:1.4;transition:border-color .15s,color .15s}
.sug:hover{border-color:rgba(201,100,66,.4);color:var(--text)}

/* Message list */
.msg-list{display:flex;flex-direction:column;padding:8px 0 4px}

/* ── Message groups ── */
.mg{padding:3px 12px 3px}
/* User turn */
.mg.user{display:flex;flex-direction:column;align-items:flex-end}
.mg.user .mg-bubble{background:var(--user-bg);border:1px solid rgba(201,100,66,.18);
  border-radius:10px 10px 2px 10px;padding:7px 11px;max-width:92%;
  font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-word;
  color:var(--text)}
/* AI turn */
.mg.ai{display:flex;flex-direction:column;align-items:flex-start}
.mg-author{font-size:10px;font-weight:700;letter-spacing:.05em;margin-bottom:3px;
           color:var(--muted)}
.mg.ai .mg-author{color:var(--accent)}
.mg.ai .mg-bubble{font-size:13px;line-height:1.7;white-space:pre-wrap;
  word-break:break-word;color:var(--text);max-width:100%}
.mg.ai .mg-bubble.streaming::after{content:'▋';animation:blink .7s step-end infinite;
  color:var(--accent);font-size:12px;margin-left:1px}
@keyframes blink{50%{opacity:0}}
/* Error */
.mg.err .mg-bubble{color:#f98a7a;font-size:12px}

/* Code block */
.code-wrap{margin-top:6px;border-radius:6px;overflow:hidden;
           border:1px solid var(--border)}
.code-hdr{display:flex;align-items:center;justify-content:space-between;
          padding:4px 10px;background:rgba(255,255,255,.04);
          font-size:10px;color:var(--muted);letter-spacing:.04em}
.copy-btn{background:none;border:none;color:var(--muted);cursor:pointer;
          font-size:10px;font-family:var(--font);padding:1px 4px;border-radius:3px;
          transition:color .15s,background .15s}
.copy-btn:hover{color:var(--text);background:rgba(255,255,255,.08)}
.code-body{font-family:var(--mono);font-size:11.5px;background:var(--code-bg);
           padding:10px 12px;overflow-x:auto;white-space:pre;line-height:1.55;
           color:#d4d4d4}

/* ── Thinking indicator ── */
.thinking-row{display:flex;align-items:center;gap:7px;padding:4px 12px;
              color:var(--muted);font-size:12px;flex-shrink:0}
.thinking-dots{display:flex;gap:3px}
.thinking-dots span{width:5px;height:5px;border-radius:50%;background:var(--muted);
  animation:tdot 1.2s ease-in-out infinite}
.thinking-dots span:nth-child(2){animation-delay:.2s}
.thinking-dots span:nth-child(3){animation-delay:.4s}
@keyframes tdot{0%,80%,100%{transform:scale(.6);opacity:.4}
                40%{transform:scale(1);opacity:1}}

/* ── Input ── */
#inp-wrap{padding:6px 10px 10px;flex-shrink:0;
          border-top:1px solid var(--border)}
#inp-box{background:var(--surface);border:1px solid var(--border);
         border-radius:10px;transition:border-color .15s;overflow:hidden}
#inp-box:focus-within{border-color:rgba(201,100,66,.45)}
#inp-top{display:flex;align-items:flex-end;padding:8px 8px 8px 12px;gap:6px}
#msg-inp{flex:1;background:none;border:none;outline:none;color:var(--text);
         font-family:var(--font);font-size:13px;line-height:1.5;
         resize:none;min-height:20px;max-height:120px;overflow-y:auto}
#msg-inp::placeholder{color:var(--muted)}
#send{width:30px;height:30px;background:var(--accent);color:#fff;border:none;
      border-radius:7px;cursor:pointer;display:flex;align-items:center;
      justify-content:center;flex-shrink:0;transition:background .15s,opacity .15s}
#send:hover{background:var(--accent-h)}
#send:disabled{opacity:.35;cursor:not-allowed;pointer-events:none}
/* bottom bar inside inp-box */
#inp-btm{display:flex;align-items:center;gap:4px;padding:0 8px 6px 10px}
.aibtn{background:none;border:1px solid var(--border);border-radius:4px;
       color:var(--muted);cursor:pointer;padding:2px 7px;font-size:11px;
       font-family:var(--font);display:flex;align-items:center;gap:3px;
       transition:color .15s,border-color .15s,background .15s;white-space:nowrap}
.aibtn:hover{color:var(--text);border-color:rgba(255,255,255,.18);
             background:rgba(255,255,255,.05)}
.aibtn.hi{color:var(--accent);border-color:rgba(201,100,66,.35);
          background:rgba(201,100,66,.06)}
.inp-spacer{flex:1}
.inp-hint{font-size:10px;color:var(--muted);opacity:.6;user-select:none}

/* ── Attachment preview strip ── */
#img-preview{display:none;flex-wrap:wrap;gap:6px;padding:8px 12px 0;
             border-bottom:1px solid var(--border)}
#img-preview.has-images{display:flex}
/* Image thumbnail */
.img-thumb{position:relative;width:60px;height:60px;flex-shrink:0;border-radius:6px;
           overflow:hidden;border:1px solid var(--border)}
.img-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.img-thumb .rm,.file-chip .rm{background:none;border:none;color:var(--muted);
               cursor:pointer;font-size:13px;line-height:1;padding:0 2px;
               flex-shrink:0;transition:color .15s}
.img-thumb .rm{position:absolute;top:2px;right:2px;width:16px;height:16px;
               background:rgba(0,0,0,.7);border-radius:50%;color:#fff;font-size:10px;
               display:flex;align-items:center;justify-content:center}
.img-thumb .rm:hover{background:rgba(220,60,60,.85)}
.file-chip .rm:hover{color:#f85149}
/* Text/binary file chip */
.file-chip{display:flex;align-items:center;gap:5px;padding:4px 8px;
           background:var(--surface);border:1px solid var(--border);border-radius:6px;
           font-size:11px;color:var(--text);max-width:180px}
.file-chip .fc-icon{color:var(--accent);flex-shrink:0}
.file-chip .fc-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.file-chip .fc-size{color:var(--muted);flex-shrink:0;font-size:10px}
/* Attachments shown inside user bubble */
.bubble-files{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:5px}
.bubble-files img{max-width:180px;max-height:140px;border-radius:5px;
                  display:block;object-fit:cover;border:1px solid rgba(255,255,255,.1)}
.bubble-file-chip{display:flex;align-items:center;gap:4px;padding:3px 7px;
                  background:rgba(255,255,255,.06);border:1px solid var(--border);
                  border-radius:5px;font-size:11px;color:var(--muted)}

/* Scrollbar */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:2px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.22)}
</style>
</head>
<body>

<!-- Header -->
<div id="hdr">
  <div class="logo">
    <svg class="logo-ic" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
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
    <span class="logo-name">FPGA Vibe</span>
  </div>
  <span id="model-badge">claude</span>
  <div class="hdr-btns">
    <button class="ibtn" id="cfg-btn" title="Settings">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
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
    <button class="ibtn" id="clear-btn" title="New conversation">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14zm0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16z"/>
        <path d="M8 4a.5.5 0 0 1 .5.5v3h3a.5.5 0 0 1 0 1h-3v3a.5.5 0 0 1-1 0v-3h-3a.5.5 0 0 1 0-1h3v-3A.5.5 0 0 1 8 4z"/>
      </svg>
    </button>
  </div>
</div>

<!-- Settings drawer -->
<div id="cfg-drawer">
  <div class="cfg-body">

    <div class="cfg-sec">
      <div class="cfg-ttl">Model</div>
      <div class="fld">
        <select id="cfg-backend">
          <option value="claude">Claude Sonnet (default)</option>
          <option value="claude-opus">Claude Opus</option>
          <option value="claude-haiku">Claude Haiku (fast)</option>
          <option value="ollama">Ollama (local)</option>
          <option value="rtlcoder">RTLCoder (HDL fine-tuned)</option>
          <option value="codev">CodeV (HDL fine-tuned)</option>
        </select>
      </div>
    </div>

    <div class="cfg-sec">
      <div class="cfg-ttl">LLM Router</div>
      <div class="fld">
        <label>URL</label>
        <input id="cfg-url" type="text" placeholder="http://localhost:8765">
      </div>
      <div class="fld">
        <label>API Key</label>
        <input id="cfg-key" type="password" placeholder="sk-ant-… (leave blank to keep)">
      </div>
    </div>

    <div class="btn-row">
      <button class="btn-save" id="save-btn">Save</button>
      <span class="cfg-ok" id="cfg-ok">✓ Saved</span>
    </div>

    <hr class="sep">

    <div class="cfg-sec">
      <div class="cfg-ttl">MCP Services</div>
      <div id="mcp-list"></div>
    </div>

  </div>
</div>

<!-- Messages -->
<div id="msgs">
  <div id="empty">
    <svg class="empty-icon" width="48" height="48" viewBox="0 0 56 56" fill="none">
      <rect x="14" y="14" width="28" height="28" rx="3.5" fill="#c96442" opacity=".9"/>
      <rect x="19" y="8"  width="4" height="6" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="26" y="8"  width="4" height="6" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="33" y="8"  width="4" height="6" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="19" y="42" width="4" height="6" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="26" y="42" width="4" height="6" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="33" y="42" width="4" height="6" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="8"  y="19" width="6" height="4" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="8"  y="26" width="6" height="4" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="8"  y="33" width="6" height="4" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="42" y="19" width="6" height="4" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="42" y="26" width="6" height="4" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="42" y="33" width="6" height="4" rx="1" fill="#c96442" opacity=".6"/>
      <rect x="19" y="19" width="7" height="7" rx="1.5" fill="rgba(0,0,0,.35)"/>
      <rect x="30" y="19" width="7" height="7" rx="1.5" fill="rgba(0,0,0,.35)"/>
      <rect x="19" y="30" width="7" height="7" rx="1.5" fill="rgba(0,0,0,.35)"/>
      <rect x="30" y="30" width="7" height="7" rx="1.5" fill="rgba(0,0,0,.35)"/>
      <circle cx="28" cy="28" r="3.5" fill="rgba(255,255,255,.25)"/>
    </svg>
    <div>
      <div class="empty-title">FPGA Vibe Coding</div>
      <div class="empty-sub" style="margin-top:4px">Describe your design or ask about your RTL code</div>
    </div>
    <div class="suggestions">
      <button class="sug" data-sug="Design an 8-bit counter with async reset">Design an 8-bit counter with async reset</button>
      <button class="sug" data-sug="Explain the clock domain crossing in this module">Explain the clock domain crossing in this module</button>
      <button class="sug" data-sug="Generate a PWM controller in SystemVerilog">Generate a PWM controller in SystemVerilog</button>
    </div>
  </div>
</div>

<!-- Hidden file input for attachment -->
<input type="file" id="img-file-input" multiple style="display:none">

<!-- Input -->
<div id="inp-wrap">
  <div id="inp-box">
    <!-- Image preview strip (shown only when images are attached) -->
    <div id="img-preview"></div>
    <div id="inp-top">
      <textarea id="msg-inp" rows="1"
                placeholder="Message FPGA Vibe…"></textarea>
      <button id="send" title="Send (Enter)">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 15a.5.5 0 0 0 .5-.5V2.707l3.146 3.147a.5.5 0 0 0
            .708-.708l-4-4a.5.5 0 0 0-.708 0l-4 4a.5.5 0 1 0 .708.708L7.5
            2.707V14.5a.5.5 0 0 0 .5.5z"/>
        </svg>
      </button>
    </div>
    <div id="inp-btm">
      <button class="aibtn" id="attach-btn" title="Attach file">
        <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor">
          <path d="M4.5 3a2.5 2.5 0 0 1 5 0v9a1.5 1.5 0 0 1-3 0V5a.5.5 0 0 1 1
            0v7a.5.5 0 0 0 1 0V3a1.5 1.5 0 1 0-3 0v9a2.5 2.5 0 0 0 5 0V5a.5.5 0
            0 1 1 0v7a3.5 3.5 0 1 1-7 0z"/>
        </svg>
        Attach
      </button>
      <button class="aibtn" id="spec-rtl-btn" title="Generate RTL from spec in input">
        <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor">
          <path d="M11.251.068a.5.5 0 0 1 .227.58L9.677 6.5H13a.5.5 0 0 1
            .364.843l-8 8.5a.5.5 0 0 1-.842-.49L6.323 9.5H3a.5.5 0 0 1-.364-.843l8-8.5a.5.5
            0 0 1 .615-.09z"/>
        </svg>
        Spec→RTL
      </button>
      <div class="inp-spacer"></div>
      <span class="inp-hint">Enter↵ send · Shift+Enter newline</span>
    </div>
  </div>
</div>

<script nonce="${n}">
const vscode    = acquireVsCodeApi();
const msgsEl    = document.getElementById('msgs');
const emptyEl   = document.getElementById('empty');
const sendBtn   = document.getElementById('send');
const cfgDrawer = document.getElementById('cfg-drawer');
const cfgBtn    = document.getElementById('cfg-btn');
const MCP       = ${mcpDefs};

let hasMsgs       = false;
let msgList       = null;
let streaming     = null;
let cfgOpen       = false;
let busy          = false;
// Each entry: {kind:'image'|'text'|'binary', name, size, mimeType,
//              dataUrl? (image), base64? (image/binary), content? (text)}
let attachedFiles = [];

// ── Settings ──────────────────────────────────────────────────────────────────
function toggleCfg() {
  cfgOpen = !cfgOpen;
  cfgDrawer.classList.toggle('open', cfgOpen);
  cfgBtn.classList.toggle('on', cfgOpen);
}
function updateBadge() {
  const sel = document.getElementById('cfg-backend');
  document.getElementById('model-badge').textContent =
    sel.options[sel.selectedIndex].text.split(' ')[0].toLowerCase();
}
function saveCfg() {
  vscode.postMessage({ type:'saveConfig', config:{
    backend:   document.getElementById('cfg-backend').value,
    routerUrl: document.getElementById('cfg-url').value,
    apiKey:    document.getElementById('cfg-key').value,
  }});
  updateBadge();
}

// ── MCP list ──────────────────────────────────────────────────────────────────
(function buildMcp() {
  const list = document.getElementById('mcp-list');
  for (const s of MCP) {
    const dot  = document.createElement('span'); dot.className = 'dot stopped'; dot.id = 'd-' + s.id;
    const name = document.createElement('span'); name.className = 'mcp-name'; name.textContent = s.label;
    const btn  = document.createElement('button'); btn.className = 'mcp-btn'; btn.id = 'b-' + s.id;
    btn.textContent = 'Start';
    btn.addEventListener('click', function() { vscode.postMessage({ type:'toggleMcp', id: s.id }); });
    const row = document.createElement('div'); row.className = 'mcp-row';
    row.appendChild(dot); row.appendChild(name); row.appendChild(btn);
    list.appendChild(row);
  }
})();
function applyMcp(id, st) {
  const d = document.getElementById('d-' + id);
  const b = document.getElementById('b-' + id);
  if (!d || !b) return;
  d.className = 'dot ' + st;
  const on = st === 'running' || st === 'starting';
  b.textContent = on ? 'Stop' : 'Start';
  b.classList.toggle('stop', on);
}

// ── Thinking indicator ────────────────────────────────────────────────────────
let thinkingEl = null;
function showThinking(txt) {
  if (thinkingEl) { thinkingEl.querySelector('span:last-child').textContent = txt || 'Thinking…'; return; }
  thinkingEl = document.createElement('div');
  thinkingEl.className = 'thinking-row';
  const dots = document.createElement('div'); dots.className = 'thinking-dots';
  dots.innerHTML = '<span></span><span></span><span></span>';
  const label = document.createElement('span'); label.textContent = txt || 'Thinking…';
  thinkingEl.appendChild(dots); thinkingEl.appendChild(label);
  msgsEl.appendChild(thinkingEl);
  msgsEl.scrollTop = msgsEl.scrollHeight;
}
function hideThinking() {
  if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
}

// ── Chat helpers ──────────────────────────────────────────────────────────────
function clearChat() {
  msgsEl.innerHTML = '';
  msgsEl.appendChild(emptyEl);
  emptyEl.style.display = 'flex';
  hasMsgs = false; msgList = null; streaming = null;
  thinkingEl = null;
  attachedFiles = []; renderPreviews();
  setBusy(false);
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
  const g = document.createElement('div');
  if (role === 'user') {
    g.className = 'mg user';
    const b = document.createElement('div'); b.className = 'mg-bubble';
    b.textContent = text;
    g.appendChild(b);
  } else {
    g.className = role === 'err' ? 'mg err' : 'mg ai';
    const auth = document.createElement('div'); auth.className = 'mg-author';
    auth.textContent = role === 'ai' ? 'FPGA Vibe' : 'Error';
    const b = document.createElement('div');
    b.className = 'mg-bubble' + (role === 'err' ? ' err' : '');
    b.textContent = text;
    g.appendChild(auth); g.appendChild(b);
  }
  list.appendChild(g);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return g.querySelector('.mg-bubble');
}

function addCodeBlock(lang, code) {
  const list = getList();
  const last = list.lastElementChild;
  const container = (last && last.classList.contains('ai'))
    ? last.querySelector('.mg-bubble') : null;
  const wrap = document.createElement('div'); wrap.className = 'code-wrap';
  const hdr  = document.createElement('div'); hdr.className  = 'code-hdr';
  const langLabel = document.createElement('span'); langLabel.textContent = lang || 'code';
  const copyBtn = document.createElement('button'); copyBtn.className = 'copy-btn';
  copyBtn.textContent = 'Copy';
  copyBtn.addEventListener('click', function() {
    navigator.clipboard.writeText(body.textContent || '').then(() => {
      copyBtn.textContent = 'Copied!';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    });
  });
  hdr.appendChild(langLabel); hdr.appendChild(copyBtn);
  const body = document.createElement('div'); body.className = 'code-body';
  body.textContent = code;
  wrap.appendChild(hdr); wrap.appendChild(body);
  if (container) { container.appendChild(wrap); }
  else { list.appendChild(wrap); }
  msgsEl.scrollTop = msgsEl.scrollHeight;
}

function setBusy(b) {
  busy = b;
  sendBtn.disabled = b;
}

// ── File attachment ───────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function renderPreviews() {
  const strip = document.getElementById('img-preview');
  strip.innerHTML = '';
  for (let i = 0; i < attachedFiles.length; i++) {
    const f  = attachedFiles[i];
    const rm = document.createElement('button'); rm.className = 'rm'; rm.textContent = '×';
    rm.title = 'Remove';
    rm.addEventListener('click', (function(idx) {
      return function() { attachedFiles.splice(idx, 1); renderPreviews(); };
    })(i));

    if (f.kind === 'image') {
      const wrap = document.createElement('div'); wrap.className = 'img-thumb';
      const el   = document.createElement('img'); el.src = f.dataUrl; el.alt = f.name;
      wrap.appendChild(el); wrap.appendChild(rm);
      strip.appendChild(wrap);
    } else {
      const chip = document.createElement('div'); chip.className = 'file-chip';
      // file icon svg
      const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      icon.setAttribute('width','12'); icon.setAttribute('height','12');
      icon.setAttribute('viewBox','0 0 16 16'); icon.setAttribute('fill','currentColor');
      icon.classList.add('fc-icon');
      const p = document.createElementNS('http://www.w3.org/2000/svg','path');
      p.setAttribute('d','M4 0a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V4.5L9.5 0H4zm0 1h5v3.5A1.5 1.5 0 0 0 10.5 6H14v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1z');
      icon.appendChild(p);
      const name = document.createElement('span'); name.className = 'fc-name'; name.textContent = f.name;
      name.title = f.name;
      const size = document.createElement('span'); size.className = 'fc-size'; size.textContent = fmtSize(f.size);
      chip.appendChild(icon); chip.appendChild(name); chip.appendChild(size); chip.appendChild(rm);
      strip.appendChild(chip);
    }
  }
  strip.classList.toggle('has-images', attachedFiles.length > 0);
}

function loadFiles(files) {
  for (const file of files) {
    const isImage = file.type.startsWith('image/');
    const reader  = new FileReader();
    if (isImage) {
      reader.onload = function(e) {
        const dataUrl = e.target.result;
        attachedFiles.push({
          kind: 'image', name: file.name, size: file.size, mimeType: file.type,
          dataUrl, base64: dataUrl.split(',')[1],
        });
        renderPreviews();
      };
      reader.readAsDataURL(file);
    } else {
      // Read as text for code/text files; fall back to base64 for binary
      const isText = /^(text\/|application\/(json|xml|javascript|typescript|x-sh))|\.(?:v|sv|vhd|vhdl|xdc|sdc|tcl|py|txt|md|log|rpt)$/i
        .test(file.type || file.name);
      reader.onload = function(e) {
        const result = e.target.result;
        if (isText) {
          attachedFiles.push({ kind: 'text', name: file.name, size: file.size, mimeType: file.type, content: result });
        } else {
          const base64 = result.split(',')[1];
          attachedFiles.push({ kind: 'binary', name: file.name, size: file.size, mimeType: file.type, base64 });
        }
        renderPreviews();
      };
      if (isText) { reader.readAsText(file); } else { reader.readAsDataURL(file); }
    }
  }
}

function send() {
  const ta = document.getElementById('msg-inp');
  const t  = ta.value.trim();
  if ((!t && attachedFiles.length === 0) || busy) return;

  // Build user bubble
  const list = getList();
  const g = document.createElement('div'); g.className = 'mg user';
  const bubble = document.createElement('div'); bubble.className = 'mg-bubble';

  if (attachedFiles.length > 0) {
    const row = document.createElement('div'); row.className = 'bubble-files';
    for (const f of attachedFiles) {
      if (f.kind === 'image') {
        const img = document.createElement('img'); img.src = f.dataUrl; img.alt = f.name;
        row.appendChild(img);
      } else {
        const chip = document.createElement('div'); chip.className = 'bubble-file-chip';
        chip.textContent = '📄 ' + f.name + ' (' + fmtSize(f.size) + ')';
        row.appendChild(chip);
      }
    }
    bubble.appendChild(row);
  }
  if (t) { bubble.appendChild(document.createTextNode(t)); }
  g.appendChild(bubble); list.appendChild(g);
  msgsEl.scrollTop = msgsEl.scrollHeight;

  // Send to extension host
  vscode.postMessage({
    type: 'sendMessage',
    text: t,
    attachments: attachedFiles.map(function(f) {
      if (f.kind === 'image')  return { kind: 'image',  name: f.name, mimeType: f.mimeType, base64: f.base64 };
      if (f.kind === 'text')   return { kind: 'text',   name: f.name, mimeType: f.mimeType, content: f.content };
      return                          { kind: 'binary', name: f.name, mimeType: f.mimeType, base64: f.base64 };
    }),
  });

  // Clear state
  ta.value = ''; resize(ta);
  attachedFiles = []; renderPreviews();
  setBusy(true); showThinking('Thinking…');
}
function specToRtl() {
  const ta = document.getElementById('msg-inp');
  const sp = ta.value.trim(); if (!sp || busy) return;
  addMsg(sp, 'user');
  ta.value = ''; resize(ta);
  setBusy(true); showThinking('Running Spec→RTL…');
  vscode.postMessage({ type:'spec2rtl', spec:sp });
}
function resize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ── Wire up static buttons ────────────────────────────────────────────────────
document.getElementById('cfg-btn').addEventListener('click', toggleCfg);
document.getElementById('clear-btn').addEventListener('click', clearChat);
document.getElementById('save-btn').addEventListener('click', saveCfg);
document.getElementById('send').addEventListener('click', send);
document.getElementById('spec-rtl-btn').addEventListener('click', specToRtl);
document.getElementById('cfg-backend').addEventListener('change', updateBadge);

// Image attachment
const imgFileInput = document.getElementById('img-file-input');
document.getElementById('attach-btn').addEventListener('click', function() {
  imgFileInput.value = ''; // allow re-selecting same file
  imgFileInput.click();
});
imgFileInput.addEventListener('change', function() {
  loadFiles(this.files);
});

// Drag-and-drop onto the input box
document.getElementById('inp-box').addEventListener('dragover', function(e) {
  e.preventDefault();
  this.style.borderColor = 'rgba(201,100,66,.6)';
});
document.getElementById('inp-box').addEventListener('dragleave', function() {
  this.style.borderColor = '';
});
document.getElementById('inp-box').addEventListener('drop', function(e) {
  e.preventDefault();
  this.style.borderColor = '';
  if (e.dataTransfer && e.dataTransfer.files.length) {
    loadFiles(e.dataTransfer.files);
  }
});

// Suggestion chips
document.querySelectorAll('.sug').forEach(function(btn) {
  btn.addEventListener('click', function() {
    const ta = document.getElementById('msg-inp');
    ta.value = btn.getAttribute('data-sug') || btn.textContent.trim();
    resize(ta); ta.focus();
  });
});

const ta = document.getElementById('msg-inp');
ta.addEventListener('input', function(){ resize(this); });
ta.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

// ── Message handler ───────────────────────────────────────────────────────────
window.addEventListener('message', ev => {
  const m = ev.data;
  switch (m.type) {

    case 'init':
      document.getElementById('cfg-backend').value = m.config.backend || 'claude';
      document.getElementById('cfg-url').value     = m.config.routerUrl;
      updateBadge();
      for (const [id, st] of Object.entries(m.mcpStatuses || {})) applyMcp(id, st);
      break;

    case 'mcpStatus': applyMcp(m.id, m.status); break;

    case 'configSaved': {
      const ok = document.getElementById('cfg-ok');
      ok.classList.add('show');
      setTimeout(() => ok.classList.remove('show'), 2000);
      break;
    }

    case 'streamChunk':
      hideThinking();
      if (!streaming) {
        const list = getList();
        const g = document.createElement('div'); g.className = 'mg ai';
        const auth = document.createElement('div'); auth.className = 'mg-author';
        auth.textContent = 'FPGA Vibe';
        streaming = document.createElement('div');
        streaming.className = 'mg-bubble streaming';
        g.appendChild(auth); g.appendChild(streaming); list.appendChild(g);
      }
      streaming.textContent += m.chunk;
      msgsEl.scrollTop = msgsEl.scrollHeight;
      break;

    case 'streamEnd':
      if (streaming) { streaming.classList.remove('streaming'); streaming = null; }
      hideThinking(); setBusy(false); break;

    case 'response':
      hideThinking();
      addMsg(m.text, m.role === 'error' ? 'err' : 'ai');
      setBusy(false); break;

    case 'statusUpdate':
      hideThinking();
      if (m.text) showThinking(m.text);
      break;

    case 'spec2rtlResult': {
      hideThinking();
      const rv = m.result;
      const b = addMsg(
        'Module: ' + rv.module_name +
        '   Score: ' + rv.score.toFixed(0) + '/100   ' +
        (rv.passed ? '✓ PASS' : '✗ FAIL'),
        'ai',
      );
      if (rv.rtl_code) addCodeBlock('verilog', rv.rtl_code);
      setBusy(false); break;
    }

    case 'prefill': {
      const ta2 = document.getElementById('msg-inp');
      ta2.value = m.text; resize(ta2); ta2.focus(); break;
    }
  }
});

vscode.postMessage({ type:'ready' });
</script>
</body>
</html>`;
    }
}
