import * as http from 'http';
import * as vscode from 'vscode';

export class ChatPanel {
    public static currentPanel: ChatPanel | undefined;
    private static readonly viewType = 'vibe4fpgaChat';

    private readonly _panel: vscode.WebviewPanel;
    private readonly _extensionUri: vscode.Uri;
    private _disposables: vscode.Disposable[] = [];

    public static createOrShow(extensionUri: vscode.Uri) {
        const column = vscode.window.activeTextEditor?.viewColumn;

        if (ChatPanel.currentPanel) {
            ChatPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            ChatPanel.viewType,
            'FPGA Vibe Chat',
            column || vscode.ViewColumn.Beside,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'media')],
            },
        );

        ChatPanel.currentPanel = new ChatPanel(panel, extensionUri);
    }

    /** Send a message to the WebView (pre-fill input, stream chunk, etc.). */
    public static postMessage(message: Record<string, unknown>) {
        ChatPanel.currentPanel?._panel.webview.postMessage(message);
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
        this._panel = panel;
        this._extensionUri = extensionUri;

        this._panel.webview.html = this._getHtml();
        this._panel.onDidDispose(() => this.dispose(), null, this._disposables);

        this._panel.webview.onDidReceiveMessage(
            async (message) => {
                switch (message.type) {
                    case 'sendMessage':
                        await this._handleUserMessage(message.text);
                        break;
                    case 'spec2rtl':
                        await this._handleSpec2RTL(message.spec);
                        break;
                }
            },
            null,
            this._disposables,
        );
    }

    private async _handleUserMessage(text: string): Promise<void> {
        const config = vscode.workspace.getConfiguration('vibe4fpga');
        const routerUrl = config.get<string>('llmRouter.url', 'http://localhost:8765');
        const model = config.get<string>('llm.backend', 'claude');

        const body = JSON.stringify({
            messages: [{ role: 'user', content: text }],
            model,
            stream: true,
        });

        return this._streamRequest(`${routerUrl}/chat/stream`, body);
    }

    private async _handleSpec2RTL(spec: string): Promise<void> {
        const config = vscode.workspace.getConfiguration('vibe4fpga');
        const routerUrl = config.get<string>('llmRouter.url', 'http://localhost:8765');
        const model = config.get<string>('llm.backend', 'claude');

        this._panel.webview.postMessage({ type: 'statusUpdate', text: 'Running Spec2RTL pipeline...' });

        const body = JSON.stringify({
            spec,
            model,
            project_path: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
        });

        try {
            const result = await this._postJson(`${routerUrl}/skill/spec2rtl`, body);
            this._panel.webview.postMessage({
                type: 'spec2rtlResult',
                result,
            });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            this._panel.webview.postMessage({
                type: 'response',
                text: `Spec2RTL error: ${msg}`,
                role: 'error',
            });
        }
    }

    /** POST JSON body, return parsed response. */
    private _postJson(url: string, body: string): Promise<unknown> {
        return new Promise((resolve, reject) => {
            const parsed = new URL(url);
            const options: http.RequestOptions = {
                hostname: parsed.hostname,
                port: parseInt(parsed.port || '8765', 10),
                path: parsed.pathname,
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(body),
                },
            };

            const req = http.request(options, (res) => {
                const chunks: Buffer[] = [];
                res.on('data', (chunk: Buffer) => chunks.push(chunk));
                res.on('end', () => {
                    try {
                        resolve(JSON.parse(Buffer.concat(chunks).toString()));
                    } catch (e) {
                        reject(e);
                    }
                });
            });

            req.on('error', reject);
            req.write(body);
            req.end();
        });
    }

    /** Stream SSE response from /chat/stream, forwarding chunks to WebView. */
    private _streamRequest(url: string, body: string): Promise<void> {
        return new Promise((resolve) => {
            const parsed = new URL(url);
            const options: http.RequestOptions = {
                hostname: parsed.hostname,
                port: parseInt(parsed.port || '8765', 10),
                path: parsed.pathname,
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(body),
                },
            };

            const req = http.request(options, (res) => {
                let buffer = '';

                res.on('data', (chunk: Buffer) => {
                    buffer += chunk.toString();
                    const lines = buffer.split('\n');
                    buffer = lines.pop() ?? '';   // keep incomplete last line

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        const data = line.slice(6).trim();

                        if (data === '[DONE]') {
                            this._panel.webview.postMessage({ type: 'streamEnd' });
                            resolve();
                            return;
                        }

                        try {
                            const parsed = JSON.parse(data) as { chunk?: string; error?: string };
                            if (parsed.chunk) {
                                this._panel.webview.postMessage({
                                    type: 'streamChunk',
                                    chunk: parsed.chunk,
                                });
                            } else if (parsed.error) {
                                this._panel.webview.postMessage({
                                    type: 'response',
                                    text: `Error: ${parsed.error}`,
                                    role: 'error',
                                });
                            }
                        } catch {
                            // ignore malformed SSE lines
                        }
                    }
                });

                res.on('end', resolve);
            });

            req.on('error', (err) => {
                const routerUrl = vscode.workspace
                    .getConfiguration('vibe4fpga')
                    .get<string>('llmRouter.url', 'http://localhost:8765');
                this._panel.webview.postMessage({
                    type: 'response',
                    text: (
                        `Cannot connect to LLM Router (${routerUrl})\n\n` +
                        `Error: ${err.message}\n\n` +
                        `Start the router with:\n  make dev-router`
                    ),
                    role: 'error',
                });
                resolve();
            });

            req.write(body);
            req.end();
        });
    }

    private _getHtml(): string {
        return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FPGA Vibe Chat</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
      height: 100vh;
      display: flex;
      flex-direction: column;
    }
    #header {
      padding: 8px 12px;
      background: var(--vscode-sideBarSectionHeader-background);
      border-bottom: 1px solid var(--vscode-panel-border);
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    #status { font-size: 10px; color: var(--vscode-descriptionForeground); font-weight: normal; }
    #messages {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .message {
      padding: 8px 10px;
      border-radius: 4px;
      max-width: 92%;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 13px;
      line-height: 1.5;
    }
    .user       { background: var(--vscode-inputValidation-infoBackground); align-self: flex-end; }
    .assistant  { background: var(--vscode-editor-inactiveSelectionBackground); align-self: flex-start; }
    .error      { background: var(--vscode-inputValidation-errorBackground); align-self: flex-start; }
    .streaming  { background: var(--vscode-editor-inactiveSelectionBackground); align-self: flex-start; border-left: 2px solid var(--vscode-progressBar-background); }
    .rtl-block  { font-family: var(--vscode-editor-font-family, monospace); font-size: 12px; }
    .placeholder { color: var(--vscode-descriptionForeground); font-style: italic; align-self: center; margin-top: 24px; text-align: center; line-height: 1.8; }
    #toolbar {
      display: flex;
      gap: 4px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--vscode-panel-border);
    }
    .tool-btn {
      padding: 3px 8px;
      font-size: 11px;
      background: var(--vscode-button-secondaryBackground, #3a3d41);
      color: var(--vscode-button-secondaryForeground, #ccc);
      border: none;
      border-radius: 2px;
      cursor: pointer;
    }
    .tool-btn:hover { background: var(--vscode-button-secondaryHoverBackground, #4a4d51); }
    #input-area {
      display: flex;
      gap: 6px;
      padding: 8px;
      border-top: 1px solid var(--vscode-panel-border);
    }
    #message-input {
      flex: 1;
      resize: none;
      padding: 6px 8px;
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border: 1px solid var(--vscode-input-border);
      border-radius: 2px;
      font-family: inherit;
      font-size: inherit;
      height: 60px;
    }
    #send-btn {
      padding: 6px 14px;
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      border-radius: 2px;
      cursor: pointer;
      align-self: flex-end;
    }
    #send-btn:hover  { background: var(--vscode-button-hoverBackground); }
    #send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .score-pass { color: #4caf50; }
    .score-warn { color: #ff9800; }
    .score-fail { color: #f44336; }
  </style>
</head>
<body>
  <div id="header">
    FPGA Vibe Coding
    <span id="status"></span>
  </div>
  <div id="toolbar">
    <button class="tool-btn" onclick="triggerSpec2RTL()" title="Convert selection/input to RTL">⚡ Spec→RTL</button>
    <button class="tool-btn" onclick="clearChat()" title="Clear chat">✕ Clear</button>
  </div>
  <div id="messages">
    <div class="placeholder">
      用自然语言描述你的 FPGA 设计需求<br>
      <small>例：设计一个带异步复位的 FIFO，深度 16，宽度 8 bit</small><br>
      <small>或点击 ⚡ Spec→RTL 直接生成 RTL</small>
    </div>
  </div>
  <div id="input-area">
    <textarea id="message-input" placeholder="描述你的设计意图... (Ctrl+Enter 发送)"></textarea>
    <button id="send-btn" onclick="sendMessage()">发送</button>
  </div>

  <script>
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const sendBtn    = document.getElementById('send-btn');
    const statusEl   = document.getElementById('status');
    let firstMessage  = true;
    let streamingDiv  = null;

    function setStatus(text) { statusEl.textContent = text; }

    function clearChat() {
      messagesEl.innerHTML = '<div class="placeholder">用自然语言描述你的 FPGA 设计需求...</div>';
      firstMessage = true;
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
      const text = input.value.trim();
      if (!text) return;
      addMessage(text, 'user');
      vscode.postMessage({ type: 'sendMessage', text });
      input.value = '';
      sendBtn.disabled = true;
      setStatus('Thinking...');
    }

    function triggerSpec2RTL() {
      const input = document.getElementById('message-input');
      const spec = input.value.trim();
      if (!spec) { alert('请先在输入框中描述设计需求'); return; }
      addMessage(spec, 'user');
      input.value = '';
      sendBtn.disabled = true;
      setStatus('Running Spec2RTL...');
      vscode.postMessage({ type: 'spec2rtl', spec });
    }

    document.getElementById('message-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) sendMessage();
    });

    window.addEventListener('message', (event) => {
      const msg = event.data;

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
        const scoreClass = r.score >= 85 ? 'score-pass' : r.score >= 60 ? 'score-warn' : 'score-fail';
        let summary = \`=== Spec2RTL Result ===\n\`;
        summary += \`Module: \${r.module_name}  Score: \${r.score.toFixed(0)}/100\n\`;
        summary += \`Status: \${r.passed ? '✓ PASS' : '✗ FAIL'}\n\`;
        if (r.declared_decisions?.length) {
          summary += \`\nAutonomous decisions:\n\`;
          r.declared_decisions.forEach(d => { summary += \`  • \${d}\n\`; });
        }
        summary += \`\n--- RTL Code ---\n\${r.rtl_code}\`;

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
  </script>
</body>
</html>`;
    }

    public dispose() {
        ChatPanel.currentPanel = undefined;
        this._panel.dispose();
        while (this._disposables.length) {
            const d = this._disposables.pop();
            if (d) d.dispose();
        }
    }
}
