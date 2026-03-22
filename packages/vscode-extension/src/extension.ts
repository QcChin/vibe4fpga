import * as vscode from 'vscode';
import { ChatPanel } from './panels/ChatPanel';
import { COMMANDS } from './commands';

export function activate(context: vscode.ExtensionContext) {
    console.log('FPGA Vibe Coding IDE is now active');

    context.subscriptions.push(
        vscode.commands.registerCommand(COMMANDS.OPEN_CHAT, () => {
            ChatPanel.createOrShow(context.extensionUri);
        }),

        vscode.commands.registerCommand(COMMANDS.SPEC2RTL, async () => {
            const editor = vscode.window.activeTextEditor;
            const spec = editor?.document.getText(editor.selection) || '';
            ChatPanel.createOrShow(context.extensionUri);
            if (spec) {
                ChatPanel.postMessage({ type: 'prefill', text: `[Spec2RTL] ${spec}` });
            }
            // TODO: Phase 1 — invoke Spec2RTL skill via LLM Router
        }),

        vscode.commands.registerCommand(COMMANDS.CODE_REVIEW, async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('Please open an RTL file first.');
                return;
            }
            const code = editor.document.getText();
            ChatPanel.createOrShow(context.extensionUri);
            ChatPanel.postMessage({ type: 'prefill', text: `[CodeReview] ${editor.document.fileName}` });
            // TODO: Phase 1 — invoke CodeReview skill via LLM Router
        }),

        vscode.commands.registerCommand(COMMANDS.TIMING_FIX, async () => {
            // TODO: Phase 2 — invoke TimingFix skill
            vscode.window.showInformationMessage('TimingFix: available in Phase 2');
        }),

        vscode.commands.registerCommand(COMMANDS.WAVEFORM_DEBUG, async () => {
            // TODO: Phase 2 — invoke WaveformDebug skill via waveform-mcp
            vscode.window.showInformationMessage('WaveformDebug: available in Phase 2');
        }),
    );
}

export function deactivate() {}
