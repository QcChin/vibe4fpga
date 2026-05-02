import * as vscode from 'vscode';
import { ChatViewProvider } from './panels/ChatViewProvider';
import { WaveformViewProvider } from './panels/WaveformViewProvider';
import { McpManager } from './services/McpManager';
import { COMMANDS } from './commands';

export function activate(context: vscode.ExtensionContext) {
    console.log('FPGA Vibe Coding IDE is now active');

    // ── MCP process manager ──────────────────────────────────────────────────
    const mcpManager = new McpManager((id, status) => {
        provider.notifyMcpStatus(id, status);
    });

    // ── Sidebar chat view provider ───────────────────────────────────────────
    const provider = new ChatViewProvider(context.extensionUri, mcpManager);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            ChatViewProvider.viewType,
            provider,
            { webviewOptions: { retainContextWhenHidden: true } },
        ),
    );

    // ── Waveform / signal visualization panel ────────────────────────────────
    const waveformProvider = new WaveformViewProvider(
        context.extensionUri,
        () => vscode.workspace.getConfiguration('vibe4fpga').get<string>('llmRouter.url', 'http://localhost:8765'),
    );
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            WaveformViewProvider.viewType,
            waveformProvider,
            { webviewOptions: { retainContextWhenHidden: true } },
        ),
    );

    // ── Commands ─────────────────────────────────────────────────────────────
    context.subscriptions.push(

        vscode.commands.registerCommand(COMMANDS.OPEN_CHAT, () => {
            // Focus / reveal the sidebar chat panel
            provider.focus();
        }),

        vscode.commands.registerCommand(COMMANDS.SPEC2RTL, async () => {
            provider.focus();
            const editor = vscode.window.activeTextEditor;
            const spec   = editor?.document.getText(editor.selection) || '';
            if (spec) {
                provider.postMessage({ type: 'prefill', text: `[Spec2RTL] ${spec}` });
            }
        }),

        vscode.commands.registerCommand(COMMANDS.CODE_REVIEW, async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('Please open an RTL file first.');
                return;
            }
            provider.focus();
            provider.postMessage({
                type: 'prefill',
                text: `[CodeReview] ${editor.document.fileName}`,
            });
        }),

        vscode.commands.registerCommand(COMMANDS.TIMING_FIX, async () => {
            vscode.window.showInformationMessage('TimingFix: available in Phase 2');
        }),

        vscode.commands.registerCommand(COMMANDS.WAVEFORM_DEBUG, async () => {
            waveformProvider.focus();
        }),
    );

    // ── Cleanup ──────────────────────────────────────────────────────────────
    context.subscriptions.push({ dispose: () => mcpManager.dispose() });
}

export function deactivate() {}
