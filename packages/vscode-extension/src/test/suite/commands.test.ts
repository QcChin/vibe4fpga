/**
 * commands.test.ts
 *
 * Verifies that COMMANDS constants match the contributes.commands in package.json
 * and that all command IDs are registered in the extension host.
 */
import * as assert from 'assert';
import * as vscode from 'vscode';
import { COMMANDS } from '../../commands';

suite('Commands Constants', () => {
    const EXPECTED: Record<string, string> = {
        OPEN_CHAT:      'vibe4fpga.openChat',
        SPEC2RTL:       'vibe4fpga.spec2rtl',
        CODE_REVIEW:    'vibe4fpga.codeReview',
        TIMING_FIX:     'vibe4fpga.timingFix',
        WAVEFORM_DEBUG: 'vibe4fpga.waveformDebug',
    };

    test('COMMANDS object has all expected keys', () => {
        for (const key of Object.keys(EXPECTED)) {
            assert.ok(key in COMMANDS, `COMMANDS is missing key: ${key}`);
        }
    });

    test('COMMANDS values match package.json IDs', () => {
        for (const [key, expectedId] of Object.entries(EXPECTED)) {
            assert.strictEqual(
                (COMMANDS as Record<string, string>)[key],
                expectedId,
                `COMMANDS.${key} should be '${expectedId}'`,
            );
        }
    });

    test('All commands are registered in the extension host', async () => {
        const registered = await vscode.commands.getCommands(true);
        for (const id of Object.values(EXPECTED)) {
            assert.ok(
                registered.includes(id),
                `Command '${id}' is not registered`,
            );
        }
    });
});
