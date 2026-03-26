/**
 * chatPanel.test.ts
 *
 * Tests for ChatPanel lifecycle: create, reveal, postMessage, and dispose.
 * All tests are offline — no LLM Router connection required.
 */
import * as assert from 'assert';
import * as vscode from 'vscode';
import { ChatPanel } from '../../panels/ChatPanel';

suite('ChatPanel', () => {
    // Clean up any leftover panel between test suites
    teardown(() => {
        if (ChatPanel.currentPanel) {
            ChatPanel.currentPanel.dispose();
        }
    });

    // ── createOrShow ─────────────────────────────────────────────────────────

    test('createOrShow creates a new panel', async () => {
        assert.strictEqual(
            ChatPanel.currentPanel,
            undefined,
            'Panel should be undefined before creation',
        );

        const fakeUri = vscode.Uri.file('/fake/extension/root');
        ChatPanel.createOrShow(fakeUri);

        assert.ok(
            ChatPanel.currentPanel !== undefined,
            'ChatPanel.currentPanel should be set after createOrShow',
        );
    });

    test('createOrShow with existing panel reveals it instead of creating a new one', () => {
        const fakeUri = vscode.Uri.file('/fake/extension/root');

        // First call — creates
        ChatPanel.createOrShow(fakeUri);
        const first = ChatPanel.currentPanel;

        // Second call — should reveal, not replace
        ChatPanel.createOrShow(fakeUri);
        const second = ChatPanel.currentPanel;

        assert.strictEqual(first, second, 'Second call should reuse the existing panel');
    });

    // ── postMessage ──────────────────────────────────────────────────────────

    test('postMessage does not throw when panel exists', () => {
        const fakeUri = vscode.Uri.file('/fake/extension/root');
        ChatPanel.createOrShow(fakeUri);

        assert.doesNotThrow(() => {
            ChatPanel.postMessage({ type: 'prefill', text: 'hello' });
        });
    });

    test('postMessage does not throw when no panel exists', () => {
        // Ensure no panel
        if (ChatPanel.currentPanel) {
            ChatPanel.currentPanel.dispose();
        }

        assert.doesNotThrow(() => {
            ChatPanel.postMessage({ type: 'prefill', text: 'hello' });
        });
    });

    // ── dispose ──────────────────────────────────────────────────────────────

    test('dispose clears currentPanel', () => {
        const fakeUri = vscode.Uri.file('/fake/extension/root');
        ChatPanel.createOrShow(fakeUri);
        assert.ok(ChatPanel.currentPanel, 'Panel should exist before dispose');

        ChatPanel.currentPanel!.dispose();
        assert.strictEqual(
            ChatPanel.currentPanel,
            undefined,
            'currentPanel should be undefined after dispose',
        );
    });

    test('dispose is idempotent — second dispose does not throw', () => {
        const fakeUri = vscode.Uri.file('/fake/extension/root');
        ChatPanel.createOrShow(fakeUri);
        const panel = ChatPanel.currentPanel!;

        panel.dispose();

        assert.doesNotThrow(() => {
            panel.dispose();
        }, 'Second dispose call should not throw');
    });

    // ── openChat command integration ─────────────────────────────────────────

    test('openChat command creates the panel via ChatPanel.createOrShow', async () => {
        // Start with no panel
        if (ChatPanel.currentPanel) {
            ChatPanel.currentPanel.dispose();
        }

        await vscode.commands.executeCommand('vibe4fpga.openChat');

        assert.ok(
            ChatPanel.currentPanel !== undefined,
            'openChat should create a ChatPanel',
        );
    });
});
