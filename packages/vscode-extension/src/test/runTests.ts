import * as path from 'path';
import { runTests } from '@vscode/test-electron';

async function main() {
    // The folder containing the Extension Manifest package.json
    const extensionDevelopmentPath = path.resolve(__dirname, '../../');

    // The path to the extension test runner script
    const extensionTestsPath = path.resolve(__dirname, './suite/index');

    await runTests({
        extensionDevelopmentPath,
        extensionTestsPath,
        // --disable-extensions prevents interference from other installed extensions
        launchArgs: ['--disable-extensions'],
    });
}

main().catch(err => {
    console.error('Failed to run tests:', err);
    process.exit(1);
});
