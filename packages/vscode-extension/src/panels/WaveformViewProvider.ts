import * as http from 'http';
import * as vscode from 'vscode';

function nonce(): string {
    let s = '';
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) { s += chars[Math.floor(Math.random() * chars.length)]; }
    return s;
}

export class WaveformViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'vibe4fpga.waveformPanel';
    private _view?: vscode.WebviewView;

    constructor(
        private readonly _extensionUri: vscode.Uri,
        private readonly _routerUrl: () => string,
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
                    // no-op: waveform data is pushed externally via loadVcd / postMessage
                    break;
                case 'requestLoad':
                    await this._handleRequestLoad(msg.filePath as string | undefined);
                    break;
                case 'openFilePicker':
                    await this._handleRequestLoad(undefined);
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

    public loadVcd(filePath: string): void {
        this._view?.webview.postMessage({ type: 'loadVcd', filePath });
    }

    // ── Internal ─────────────────────────────────────────────────────────────

    private async _handleRequestLoad(filePath: string | undefined): Promise<void> {
        let targetPath = filePath;

        if (!targetPath) {
            const uris = await vscode.window.showOpenDialog({
                canSelectMany: false,
                filters: { 'VCD Files': ['vcd', 'fst', 'fsdb'] },
                openLabel: 'Load Waveform',
            });
            if (!uris || uris.length === 0) { return; }
            targetPath = uris[0].fsPath;
        }

        const routerUrl = this._routerUrl();
        const body = JSON.stringify({ file_path: targetPath });

        try {
            const result = await this._postJson(`${routerUrl}/skill/parse_waveform`, body);
            this._view?.webview.postMessage({ type: 'waveformData', ...(result as object) });
        } catch (err: unknown) {
            // Router may not be running yet; surface a clear message in the panel
            const errMsg = err instanceof Error ? err.message : String(err);
            vscode.window.showWarningMessage(`Waveform router not reachable: ${errMsg}`);
            // Post a placeholder so the UI can show a friendly error state
            this._view?.webview.postMessage({
                type: 'waveformError',
                message: `Could not contact LLM router at ${routerUrl}. Is the sidecar running?`,
            });
        }
    }

    private _postJson(url: string, body: string): Promise<unknown> {
        return new Promise((resolve, reject) => {
            const u = new URL(url);
            const req = http.request(
                {
                    hostname: u.hostname,
                    port: parseInt(u.port || '8765', 10),
                    path: u.pathname,
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Content-Length': Buffer.byteLength(body),
                    },
                },
                (res) => {
                    const chunks: Buffer[] = [];
                    res.on('data', (c: Buffer) => chunks.push(c));
                    res.on('end', () => {
                        try { resolve(JSON.parse(Buffer.concat(chunks).toString())); }
                        catch (e) { reject(e); }
                    });
                },
            );
            req.on('error', reject);
            req.write(body);
            req.end();
        });
    }

    private _html(webview: vscode.Webview): string {
        const n = nonce();
        const csp = [
            `default-src 'none'`,
            `style-src 'unsafe-inline'`,
            `script-src 'nonce-${n}'`,
            `img-src data: blob:`,
        ].join('; ');

        return /* html */`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Waveform</title>
<style>
  :root {
    --bg:      var(--vscode-sideBar-background,            #1e1e1e);
    --surface: var(--vscode-editor-background,             #252526);
    --border:  var(--vscode-panel-border,                  #3c3c3c);
    --text:    var(--vscode-foreground,                    #cccccc);
    --muted:   var(--vscode-descriptionForeground,         #848484);
    --accent:  #c96442;
    --low:     #4ec9b0;
    --high:    #c96442;
    --row-h:   24px;
    --label-w: 140px;
    --ruler-h: 22px;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; font-family: var(--vscode-font-family, 'Segoe UI', sans-serif); font-size: 12px; background: var(--bg); color: var(--text); }

  /* ── header ── */
  #header {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 8px; background: var(--surface);
    border-bottom: 1px solid var(--border); flex-shrink: 0;
  }
  #header svg { flex-shrink: 0; }
  #header .title { font-weight: 600; font-size: 13px; flex: 1; }
  .btn {
    background: transparent; border: 1px solid var(--border);
    color: var(--text); padding: 2px 8px; border-radius: 3px;
    cursor: pointer; font-size: 11px; white-space: nowrap;
  }
  .btn:hover { background: var(--border); }
  .btn-accent { border-color: var(--accent); color: var(--accent); }
  .btn-accent:hover { background: color-mix(in srgb, var(--accent) 20%, transparent); }

  /* ── zoom controls ── */
  #zoom-bar {
    display: flex; align-items: center; gap: 4px;
    padding: 3px 8px; background: var(--surface);
    border-bottom: 1px solid var(--border); flex-shrink: 0;
  }
  #zoom-bar .zoom-label { color: var(--muted); font-size: 11px; margin-right: 4px; }

  /* ── empty state ── */
  #empty-state {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 12px;
    color: var(--muted);
  }
  #empty-state .empty-icon { opacity: 0.35; }
  #empty-state .empty-text { font-size: 13px; text-align: center; max-width: 200px; line-height: 1.5; }

  /* ── main layout ── */
  #main {
    display: none; flex: 1; overflow: hidden;
    flex-direction: row; min-height: 0;
  }

  /* ── signal list ── */
  #signal-list {
    width: 35%; min-width: 120px; max-width: 220px;
    background: var(--surface); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; overflow: hidden;
  }
  #signal-list-header {
    padding: 4px 8px; font-size: 11px; font-weight: 600;
    color: var(--muted); border-bottom: 1px solid var(--border);
    flex-shrink: 0; text-transform: uppercase; letter-spacing: 0.05em;
  }
  #signal-list-body {
    flex: 1; overflow-y: auto; overflow-x: hidden;
  }
  .sig-item {
    display: flex; align-items: center; gap: 5px;
    padding: 3px 6px; border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent);
    height: var(--row-h); cursor: pointer;
  }
  .sig-item:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .sig-item input[type="checkbox"] { flex-shrink: 0; accent-color: var(--accent); cursor: pointer; }
  .sig-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
  .sig-value {
    font-size: 10px; padding: 1px 4px; border-radius: 2px;
    background: color-mix(in srgb, var(--accent) 20%, transparent);
    color: var(--accent); font-family: monospace; flex-shrink: 0;
  }

  /* ── waveform canvas area ── */
  #waveform-area {
    flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0;
  }
  #ruler-scroll { flex-shrink: 0; overflow: hidden; }
  #ruler-svg { display: block; }
  #wave-scroll {
    flex: 1; overflow: auto; position: relative;
  }
  #waveform-canvas { display: block; }

  /* ── status bar ── */
  #status-bar {
    display: flex; align-items: center; gap: 12px;
    padding: 2px 8px; background: var(--surface);
    border-top: 1px solid var(--border); flex-shrink: 0;
    font-size: 11px; color: var(--muted);
  }
  #status-bar .status-item { display: flex; gap: 4px; }
  #status-bar .status-val { color: var(--text); font-family: monospace; }

  /* ── layout wrapper ── */
  #root {
    height: 100%; display: flex; flex-direction: column;
  }
</style>
</head>
<body>
<div id="root">

  <!-- Header -->
  <div id="header">
    <!-- FPGA oscilloscope icon -->
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="2" width="14" height="10" rx="1" stroke="#c96442" stroke-width="1.2" fill="none"/>
      <polyline points="2,9 4,9 4,5 6,5 6,9 8,9 8,4 10,4 10,9 12,9 12,6 14,6" stroke="#4ec9b0" stroke-width="1.2" fill="none" stroke-linejoin="round"/>
      <line x1="4" y1="13" x2="12" y2="13" stroke="#c96442" stroke-width="1.2"/>
      <line x1="6" y1="12" x2="6" y2="14" stroke="#c96442" stroke-width="1.2"/>
      <line x1="10" y1="12" x2="10" y2="14" stroke="#c96442" stroke-width="1.2"/>
    </svg>
    <span class="title">Waveform</span>
    <button id="load-vcd-btn" class="btn btn-accent">Load VCD</button>
    <button id="clear-btn" class="btn">Clear</button>
  </div>

  <!-- Zoom bar (hidden until data loaded) -->
  <div id="zoom-bar" style="display:none">
    <span class="zoom-label">Zoom:</span>
    <button id="zoom-in-btn" class="btn" title="Zoom in">+</button>
    <button id="zoom-out-btn" class="btn" title="Zoom out">−</button>
    <button id="zoom-fit-btn" class="btn" title="Fit all">Fit</button>
    <span id="zoom-level" style="color:var(--muted);font-size:11px;margin-left:4px">1.0×</span>
  </div>

  <!-- Empty state -->
  <div id="empty-state">
    <svg class="empty-icon" width="56" height="40" viewBox="0 0 56 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="2" width="52" height="36" rx="3" stroke="currentColor" stroke-width="1.5" fill="none"/>
      <polyline points="6,28 12,28 12,12 18,12 18,28 24,28 24,8 30,8 30,28 36,28 36,16 42,16 42,28 50,28" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linejoin="round"/>
      <circle cx="28" cy="36" r="0" fill="none"/>
    </svg>
    <div class="empty-text">Load a VCD/FST file to visualize signals</div>
    <button id="open-file-btn" class="btn btn-accent">Open VCD File</button>
  </div>

  <!-- Main panel (shown after load) -->
  <div id="main">
    <!-- Signal list -->
    <div id="signal-list">
      <div id="signal-list-header">Signals</div>
      <div id="signal-list-body"></div>
    </div>

    <!-- Waveform area -->
    <div id="waveform-area">
      <div id="ruler-scroll">
        <svg id="ruler-svg" height="${24}" width="800"></svg>
      </div>
      <div id="wave-scroll">
        <svg id="waveform-canvas" width="800" height="0"></svg>
      </div>
    </div>
  </div>

  <!-- Status bar -->
  <div id="status-bar">
    <div class="status-item">Time: <span id="cursor-time" class="status-val">—</span></div>
    <div class="status-item">Signal: <span id="cursor-signal" class="status-val">—</span></div>
    <div class="status-item">Value: <span id="cursor-value" class="status-val">—</span></div>
  </div>

</div>

<script nonce="${n}">
(function () {
  'use strict';
  const vscode = acquireVsCodeApi();

  // ── DOM refs ────────────────────────────────────────────────────────────
  const emptyState   = document.getElementById('empty-state');
  const mainPanel    = document.getElementById('main');
  const zoomBar      = document.getElementById('zoom-bar');
  const signalBody   = document.getElementById('signal-list-body');
  const waveSvg      = document.getElementById('waveform-canvas');
  const rulerSvg     = document.getElementById('ruler-svg');
  const rulerScroll  = document.getElementById('ruler-scroll');
  const waveScroll   = document.getElementById('wave-scroll');
  const cursorTime   = document.getElementById('cursor-time');
  const cursorSignal = document.getElementById('cursor-signal');
  const cursorValue  = document.getElementById('cursor-value');
  const zoomLevel    = document.getElementById('zoom-level');

  // ── State ────────────────────────────────────────────────────────────────
  /** @type {{ name: string, width: number, events: {time: number, value: string}[] }[]} */
  let signals = [];
  let duration_ns = 0;
  /** which signal indices are visible */
  let visible = new Set();
  let zoom = 1.0;           // px per ns
  let BASE_PX_PER_NS = 0.1; // recalculated on load
  const ROW_H  = 24;
  const RULER_H = 22;
  const PADDING = 8;
  const MIN_PX_PER_NS = 0.001;
  const MAX_PX_PER_NS = 100;

  const COLORS = [
    '#4ec9b0', '#c96442', '#569cd6', '#dcdcaa',
    '#9cdcfe', '#ce9178', '#b5cea8', '#c586c0',
  ];

  // ── Button wiring ─────────────────────────────────────────────────────────
  document.getElementById('load-vcd-btn').addEventListener('click', () => {
    vscode.postMessage({ type: 'openFilePicker' });
  });
  document.getElementById('clear-btn').addEventListener('click', () => {
    clearAll();
  });
  document.getElementById('open-file-btn').addEventListener('click', () => {
    vscode.postMessage({ type: 'openFilePicker' });
  });
  document.getElementById('zoom-in-btn').addEventListener('click', () => {
    setZoom(zoom * 1.5);
  });
  document.getElementById('zoom-out-btn').addEventListener('click', () => {
    setZoom(zoom / 1.5);
  });
  document.getElementById('zoom-fit-btn').addEventListener('click', () => {
    fitAll();
  });

  // ── Ruler + canvas sync scroll ────────────────────────────────────────────
  waveScroll.addEventListener('scroll', () => {
    rulerScroll.scrollLeft = waveScroll.scrollLeft;
  });

  // ── Canvas click → cursor ─────────────────────────────────────────────────
  waveSvg.addEventListener('click', (e) => {
    const rect = waveSvg.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = xToTime(x);
    updateCursor(x, t);
  });

  // ── Extension message handler ─────────────────────────────────────────────
  window.addEventListener('message', (event) => {
    const msg = event.data;
    switch (msg.type) {
      case 'waveformData':
        loadWaveformData(msg);
        break;
      case 'loadVcd':
        vscode.postMessage({ type: 'requestLoad', filePath: msg.filePath });
        break;
      case 'clear':
        clearAll();
        break;
      case 'waveformError':
        showError(msg.message);
        break;
    }
  });

  // ── Notify ready ──────────────────────────────────────────────────────────
  vscode.postMessage({ type: 'ready' });

  // ── Core functions ────────────────────────────────────────────────────────

  function clearAll() {
    signals = [];
    duration_ns = 0;
    visible.clear();
    zoom = 1.0;
    signalBody.innerHTML = '';
    waveSvg.innerHTML = '';
    rulerSvg.innerHTML = '';
    waveSvg.setAttribute('width', '800');
    waveSvg.setAttribute('height', '0');
    rulerSvg.setAttribute('width', '800');
    mainPanel.style.display = 'none';
    zoomBar.style.display = 'none';
    emptyState.style.display = 'flex';
    cursorTime.textContent = '—';
    cursorSignal.textContent = '—';
    cursorValue.textContent = '—';
  }

  /**
   * @param {{ signals: {name:string, width:number, events:{time:number,value:string}[]}[], duration_ns: number }} data
   */
  function loadWaveformData(data) {
    signals = data.signals || [];
    duration_ns = data.duration_ns || 0;
    if (signals.length === 0) { return; }

    // Auto-detect duration if not provided
    if (!duration_ns) {
      for (const sig of signals) {
        for (const ev of sig.events) {
          if (ev.time > duration_ns) { duration_ns = ev.time; }
        }
      }
      duration_ns = duration_ns * 1.05 + 1; // small right-margin
    }

    visible = new Set(signals.map((_, i) => i));

    // Set base zoom so entire waveform fits in ~700px
    const availW = waveScroll.clientWidth || 700;
    BASE_PX_PER_NS = Math.max(MIN_PX_PER_NS, availW / (duration_ns || 1));
    zoom = 1.0;

    emptyState.style.display = 'none';
    mainPanel.style.display = 'flex';
    zoomBar.style.display = 'flex';

    buildSignalList();
    renderAll();
  }

  function showError(msg) {
    emptyState.style.display = 'flex';
    mainPanel.style.display = 'none';
    const txt = emptyState.querySelector('.empty-text');
    if (txt) { txt.textContent = msg; }
  }

  function buildSignalList() {
    signalBody.innerHTML = '';
    signals.forEach((sig, i) => {
      const item = document.createElement('div');
      item.className = 'sig-item';
      item.dataset.idx = String(i);

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.addEventListener('change', () => {
        if (cb.checked) { visible.add(i); } else { visible.delete(i); }
        renderAll();
      });

      const nm = document.createElement('span');
      nm.className = 'sig-name';
      nm.title = sig.name;
      nm.textContent = sig.name;

      const chip = document.createElement('span');
      chip.className = 'sig-value';
      chip.id = 'chip-' + i;
      chip.textContent = sig.events.length > 0 ? sig.events[0].value : '?';

      item.appendChild(cb);
      item.appendChild(nm);
      item.appendChild(chip);
      signalBody.appendChild(item);
    });
  }

  function setZoom(z) {
    zoom = Math.max(MIN_PX_PER_NS / BASE_PX_PER_NS, Math.min(MAX_PX_PER_NS / BASE_PX_PER_NS, z));
    zoomLevel.textContent = zoom.toFixed(2) + '×';
    renderAll();
  }

  function fitAll() {
    zoom = 1.0;
    zoomLevel.textContent = '1.00×';
    renderAll();
  }

  function pxPerNs() {
    return BASE_PX_PER_NS * zoom;
  }

  function timeToX(t) {
    return PADDING + t * pxPerNs();
  }

  function xToTime(x) {
    return (x - PADDING) / pxPerNs();
  }

  function totalWidth() {
    return PADDING * 2 + duration_ns * pxPerNs();
  }

  function renderAll() {
    const visArr = [...visible].sort((a, b) => a - b);
    const totalH = visArr.length * ROW_H;
    const w = Math.max(800, totalWidth());

    waveSvg.setAttribute('width', String(w));
    waveSvg.setAttribute('height', String(totalH));
    rulerSvg.setAttribute('width', String(w));
    rulerSvg.setAttribute('height', String(RULER_H));

    waveSvg.innerHTML = '';
    rulerSvg.innerHTML = '';

    renderRuler(w);
    visArr.forEach((sigIdx, row) => {
      renderSignalRow(sigIdx, row, w);
    });
  }

  function renderRuler(w) {
    const ppn = pxPerNs();
    // Decide tick spacing in ns: aim for ~60px between ticks
    const rawTickNs = 60 / ppn;
    const tickNs = niceNumber(rawTickNs);

    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');

    // Background
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bg.setAttribute('x', '0'); bg.setAttribute('y', '0');
    bg.setAttribute('width', String(w)); bg.setAttribute('height', String(RULER_H));
    bg.setAttribute('fill', 'var(--surface)');
    g.appendChild(bg);

    // Bottom border line
    const border = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    border.setAttribute('x1', '0'); border.setAttribute('y1', String(RULER_H - 1));
    border.setAttribute('x2', String(w)); border.setAttribute('y2', String(RULER_H - 1));
    border.setAttribute('stroke', 'var(--border)'); border.setAttribute('stroke-width', '1');
    g.appendChild(border);

    // Ticks and labels
    let t = 0;
    while (t <= duration_ns + tickNs) {
      const x = timeToX(t);
      if (x > w) { break; }

      const tick = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      tick.setAttribute('x1', String(x)); tick.setAttribute('y1', String(RULER_H - 6));
      tick.setAttribute('x2', String(x)); tick.setAttribute('y2', String(RULER_H - 1));
      tick.setAttribute('stroke', 'var(--muted)'); tick.setAttribute('stroke-width', '1');
      g.appendChild(tick);

      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', String(x + 2));
      label.setAttribute('y', String(RULER_H - 8));
      label.setAttribute('fill', 'var(--muted)');
      label.setAttribute('font-size', '9');
      label.setAttribute('font-family', 'monospace');
      label.textContent = formatTime(t);
      g.appendChild(label);

      t += tickNs;
    }

    rulerSvg.appendChild(g);
  }

  function renderSignalRow(sigIdx, row, totalW) {
    const sig = signals[sigIdx];
    const color = COLORS[sigIdx % COLORS.length];
    const y0 = row * ROW_H;
    const yTop  = y0 + 3;
    const yBot  = y0 + ROW_H - 3;
    const yMid  = y0 + ROW_H / 2;

    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');

    // Row separator
    const sep = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    sep.setAttribute('x1', '0'); sep.setAttribute('y1', String(y0 + ROW_H - 1));
    sep.setAttribute('x2', String(totalW)); sep.setAttribute('y2', String(y0 + ROW_H - 1));
    sep.setAttribute('stroke', 'var(--border)'); sep.setAttribute('stroke-width', '0.5');
    sep.setAttribute('opacity', '0.5');
    g.appendChild(sep);

    const events = sig.events;
    if (events.length === 0) {
      waveSvg.appendChild(g);
      return;
    }

    if (sig.width === 1) {
      // ── 1-bit digital signal: step SVG path ──
      const pathParts = [];
      let lastX = timeToX(0);
      let lastVal = '0';

      // Find initial value (value before first event, default 0)
      const firstT = events[0].time;
      if (firstT > 0) {
        const startY = lastVal === '1' ? yTop : yBot;
        pathParts.push('M', String(PADDING), String(startY));
        pathParts.push('H', String(timeToX(firstT)));
      }

      events.forEach((ev, ei) => {
        const x = timeToX(ev.time);
        const nextEv = events[ei + 1];
        const endX = nextEv ? timeToX(nextEv.time) : timeToX(duration_ns) + PADDING;

        const yHere = ev.value === '1' ? yTop : yBot;

        if (pathParts.length === 0) {
          pathParts.push('M', String(x), String(yHere));
        } else {
          // vertical transition
          pathParts.push('V', String(yHere));
        }
        pathParts.push('H', String(Math.min(endX, timeToX(duration_ns) + PADDING)));
        lastVal = ev.value;
        lastX = endX;
      });

      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', pathParts.join(' '));
      path.setAttribute('stroke', color);
      path.setAttribute('stroke-width', '1.5');
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke-linejoin', 'round');
      g.appendChild(path);

    } else {
      // ── Bus signal (width > 1): colored rectangles with hex labels ──
      events.forEach((ev, ei) => {
        const x1 = timeToX(ev.time);
        const nextEv = events[ei + 1];
        const x2 = nextEv ? timeToX(nextEv.time) : timeToX(duration_ns) + PADDING;
        const rectW = Math.max(1, x2 - x1 - 1);

        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', String(x1));
        rect.setAttribute('y', String(yTop));
        rect.setAttribute('width', String(rectW));
        rect.setAttribute('height', String(yBot - yTop));
        rect.setAttribute('fill', color);
        rect.setAttribute('opacity', '0.25');
        rect.setAttribute('rx', '1');
        g.appendChild(rect);

        const border = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        border.setAttribute('x', String(x1));
        border.setAttribute('y', String(yTop));
        border.setAttribute('width', String(rectW));
        border.setAttribute('height', String(yBot - yTop));
        border.setAttribute('fill', 'none');
        border.setAttribute('stroke', color);
        border.setAttribute('stroke-width', '1');
        border.setAttribute('rx', '1');
        g.appendChild(border);

        // Label if wide enough
        if (rectW > 20) {
          const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          label.setAttribute('x', String(x1 + rectW / 2));
          label.setAttribute('y', String(yMid + 3));
          label.setAttribute('fill', color);
          label.setAttribute('font-size', '9');
          label.setAttribute('font-family', 'monospace');
          label.setAttribute('text-anchor', 'middle');
          label.setAttribute('dominant-baseline', 'middle');
          // clip to rect via textLength trick – just truncate text in JS
          const hex = parseInt(ev.value, 2);
          const hexStr = isNaN(hex) ? ev.value : '0x' + hex.toString(16).toUpperCase();
          label.textContent = hexStr;
          g.appendChild(label);
        }
      });
    }

    waveSvg.appendChild(g);
  }

  // ── Cursor ─────────────────────────────────────────────────────────────────
  function updateCursor(x, t) {
    if (t < 0 || t > duration_ns) { return; }

    // Remove old cursor line
    const old = waveSvg.querySelector('.cursor-line');
    if (old) { old.remove(); }
    const oldR = rulerSvg.querySelector('.cursor-line');
    if (oldR) { oldR.remove(); }

    const h = parseInt(waveSvg.getAttribute('height') || '0', 10);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.classList.add('cursor-line');
    line.setAttribute('x1', String(x)); line.setAttribute('y1', '0');
    line.setAttribute('x2', String(x)); line.setAttribute('y2', String(h));
    line.setAttribute('stroke', '#e05252');
    line.setAttribute('stroke-width', '1');
    line.setAttribute('stroke-dasharray', '3,2');
    line.setAttribute('pointer-events', 'none');
    waveSvg.appendChild(line);

    const rLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    rLine.classList.add('cursor-line');
    rLine.setAttribute('x1', String(x)); rLine.setAttribute('y1', '0');
    rLine.setAttribute('x2', String(x)); rLine.setAttribute('y2', String(RULER_H));
    rLine.setAttribute('stroke', '#e05252');
    rLine.setAttribute('stroke-width', '1');
    rLine.setAttribute('pointer-events', 'none');
    rulerSvg.appendChild(rLine);

    cursorTime.textContent = formatTime(t);

    // Find which row was clicked
    const svgRect = waveSvg.getBoundingClientRect();
    // Determine which visible signal row was clicked by y
    const visArr = [...visible].sort((a, b) => a - b);
    const relY = (event && event.clientY) ? event.clientY - svgRect.top : -1;
    const rowIdx = relY >= 0 ? Math.floor(relY / ROW_H) : -1;
    if (rowIdx >= 0 && rowIdx < visArr.length) {
      const sigIdx = visArr[rowIdx];
      const sig = signals[sigIdx];
      cursorSignal.textContent = sig.name;
      // Find value at time t
      const val = valueAtTime(sig.events, t);
      cursorValue.textContent = val;
      // Update chip
      const chip = document.getElementById('chip-' + sigIdx);
      if (chip) { chip.textContent = val; }
    }
  }

  function valueAtTime(events, t) {
    let last = '?';
    for (const ev of events) {
      if (ev.time > t) { break; }
      last = ev.value;
    }
    return last;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function niceNumber(x) {
    if (x <= 0) { return 1; }
    const exp = Math.floor(Math.log10(x));
    const frac = x / Math.pow(10, exp);
    let nice;
    if (frac < 1.5)      { nice = 1; }
    else if (frac < 3.5) { nice = 2; }
    else if (frac < 7.5) { nice = 5; }
    else                  { nice = 10; }
    return nice * Math.pow(10, exp);
  }

  function formatTime(ns) {
    if (ns >= 1e6)  { return (ns / 1e6).toFixed(2)  + ' ms'; }
    if (ns >= 1e3)  { return (ns / 1e3).toFixed(2)  + ' µs'; }
    if (ns >= 1)    { return ns.toFixed(2)            + ' ns'; }
    return (ns * 1e3).toFixed(2) + ' ps';
  }

}());
</script>
</body>
</html>`;
    }
}
