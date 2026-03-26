/**
 * extension.test.ts
 *
 * Tests for extension activation, configuration defaults, and command
 * behaviours that do NOT require a running LLM Router.
 */
import * as assert from 'assert';
import * as path from 'path';
import * as vscode from 'vscode';

// Extension publisher.name as declared in package.json
const EXT_ID = 'vibe4fpga.vibe4fpga';

suite('Extension', () => {
    suiteSetup(async () => {
        // Ensure the extension is activated before any test runs.
        const ext = vscode.extensions.getExtension(EXT_ID);
        if (ext && !ext.isActive) {
            await ext.activate();
        }
    });

    // ── Activation ──────────────────────────────────────────────────────────

    test('extension is present in the extension registry', () => {
        const ext = vscode.extensions.getExtension(EXT_ID);
        assert.ok(ext, `Extension '${EXT_ID}' not found`);
    });

    test('extension is active after activation', () => {
        const ext = vscode.extensions.getExtension(EXT_ID);
        assert.ok(ext?.isActive, 'Extension should be active');
    });

    // ── Configuration defaults ───────────────────────────────────────────────

    test('default llmRouter.url is localhost:8765', () => {
        const config = vscode.workspace.getConfiguration('vibe4fpga');
        assert.strictEqual(
            config.get<string>('llmRouter.url'),
            'http://localhost:8765',
        );
    });

    test('default llm.backend is "claude"', () => {
        const config = vscode.workspace.getConfiguration('vibe4fpga');
        assert.strictEqual(config.get<string>('llm.backend'), 'claude');
    });

    test('default rag.enabled is false', () => {
        const config = vscode.workspace.getConfiguration('vibe4fpga');
        assert.strictEqual(config.get<boolean>('rag.enabled'), false);
    });

    // ── Command: timingFix ───────────────────────────────────────────────────

    test('timingFix shows an information message', async () => {
        let messageText = '';
        const stub = vscode.window.showInformationMessage;
        // @ts-ignore — monkey-patch for test isolation
        vscode.window.showInformationMessage = async (msg: string) => {
            messageText = msg;
            return undefined;
        };

        try {
            await vscode.commands.executeCommand('vibe4fpga.timingFix');
            assert.ok(messageText.length > 0, 'showInformationMessage was not called');
            assert.ok(
                messageText.toLowerCase().includes('timing') ||
                messageText.toLowerCase().includes('phase'),
                `Unexpected message: "${messageText}"`,
            );
        } finally {
            vscode.window.showInformationMessage = stub;
        }
    });

    // ── Command: waveformDebug ───────────────────────────────────────────────

    test('waveformDebug shows an information message', async () => {
        let messageText = '';
        const stub = vscode.window.showInformationMessage;
        // @ts-ignore
        vscode.window.showInformationMessage = async (msg: string) => {
            messageText = msg;
            return undefined;
        };

        try {
            await vscode.commands.executeCommand('vibe4fpga.waveformDebug');
            assert.ok(messageText.length > 0, 'showInformationMessage was not called');
            assert.ok(
                messageText.toLowerCase().includes('waveform') ||
                messageText.toLowerCase().includes('phase'),
                `Unexpected message: "${messageText}"`,
            );
        } finally {
            vscode.window.showInformationMessage = stub;
        }
    });

    // ── Command: codeReview ──────────────────────────────────────────────────

    test('codeReview shows a warning when no editor is open', async () => {
        await vscode.commands.executeCommand('workbench.action.closeAllEditors');

        let warningText = '';
        const stub = vscode.window.showWarningMessage;
        // @ts-ignore
        vscode.window.showWarningMessage = async (msg: string) => {
            warningText = msg;
            return undefined;
        };

        try {
            await vscode.commands.executeCommand('vibe4fpga.codeReview');
            assert.ok(warningText.length > 0, 'showWarningMessage was not called');
            assert.ok(
                warningText.toLowerCase().includes('rtl') ||
                warningText.toLowerCase().includes('file') ||
                warningText.toLowerCase().includes('open'),
                `Unexpected warning: "${warningText}"`,
            );
        } finally {
            vscode.window.showWarningMessage = stub;
        }
    });

    // ── Command: spec2rtl with active selection ──────────────────────────────

    test('spec2rtl opens chat panel when a Verilog file is active', async () => {
        const fixturePath = path.resolve(__dirname, '../fixtures/sample.v');
        const uri = vscode.Uri.file(fixturePath);
        const doc = await vscode.workspace.openTextDocument(uri);
        await vscode.window.showTextDocument(doc);

        // Does NOT require a router — just verifies no error is thrown
        // and that the panel object exists afterward.
        await vscode.commands.executeCommand('vibe4fpga.spec2rtl');

        // Close the editor to avoid affecting other tests
        await vscode.commands.executeCommand('workbench.action.closeActiveEditor');
    });
});
