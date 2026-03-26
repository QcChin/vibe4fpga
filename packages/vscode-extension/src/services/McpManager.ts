import * as path from 'path';
import { ChildProcess, spawn } from 'child_process';
import * as vscode from 'vscode';

export type McpStatus = 'stopped' | 'starting' | 'running' | 'error';

export interface McpServiceDef {
    id:      string;
    label:   string;
    subpath: string;   // relative to project root
    cmd:     string;   // uv run <cmd>
}

export const MCP_SERVICE_DEFS: McpServiceDef[] = [
    { id: 'fpga-project', label: 'FPGA Project',  subpath: 'packages/mcp-servers/fpga-project-mcp', cmd: 'fpga-project-mcp' },
    { id: 'eda-bridge',   label: 'EDA Bridge',     subpath: 'packages/mcp-servers/eda-bridge-mcp',   cmd: 'eda-bridge-mcp'   },
    { id: 'waveform',     label: 'Waveform',        subpath: 'packages/mcp-servers/waveform-mcp',     cmd: 'waveform-mcp'     },
    { id: 'instrument',   label: 'Instrument',      subpath: 'packages/mcp-servers/instrument-mcp',   cmd: 'instrument-mcp'   },
    { id: 'datasheet',    label: 'Datasheet RAG',   subpath: 'packages/mcp-servers/datasheet-mcp',    cmd: 'datasheet-mcp'    },
    { id: 'quartus',      label: 'Quartus',         subpath: 'packages/mcp-servers/quartus-mcp',      cmd: 'quartus-mcp'      },
    { id: 'yosys',        label: 'Yosys',           subpath: 'packages/mcp-servers/yosys-mcp',        cmd: 'yosys-mcp'        },
];

export class McpManager {
    private readonly _procs    = new Map<string, ChildProcess>();
    private readonly _statuses = new Map<string, McpStatus>();

    constructor(
        private readonly _onChange: (id: string, status: McpStatus) => void,
    ) {
        for (const def of MCP_SERVICE_DEFS) {
            this._statuses.set(def.id, 'stopped');
        }
    }

    private _projectRoot(): string {
        const cfg = vscode.workspace.getConfiguration('vibe4fpga');
        const configured = cfg.get<string>('projectRoot', '');
        if (configured) { return configured; }
        return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? process.cwd();
    }

    public getStatuses(): Record<string, McpStatus> {
        const out: Record<string, McpStatus> = {};
        for (const [id, s] of this._statuses) { out[id] = s; }
        return out;
    }

    public toggle(id: string): void {
        const s = this._statuses.get(id);
        if (s === 'running' || s === 'starting') {
            this.stop(id);
        } else {
            this.start(id);
        }
    }

    public start(id: string): void {
        if (this._procs.has(id)) { return; }

        const def = MCP_SERVICE_DEFS.find(d => d.id === id);
        if (!def) { return; }

        const cwd = path.join(this._projectRoot(), def.subpath);
        this._set(id, 'starting');

        const proc = spawn('uv', ['run', def.cmd], {
            cwd,
            shell: true,
            env: { ...process.env },
            stdio: 'ignore',
        });

        proc.on('spawn', () => this._set(id, 'running'));
        proc.on('error', () => {
            this._procs.delete(id);
            this._set(id, 'error');
        });
        proc.on('exit', (code) => {
            this._procs.delete(id);
            this._set(id, code === 0 ? 'stopped' : 'error');
        });

        this._procs.set(id, proc);
    }

    public stop(id: string): void {
        this._procs.get(id)?.kill();
        this._procs.delete(id);
        this._set(id, 'stopped');
    }

    private _set(id: string, status: McpStatus): void {
        this._statuses.set(id, status);
        this._onChange(id, status);
    }

    public dispose(): void {
        for (const id of [...this._procs.keys()]) { this.stop(id); }
    }
}
