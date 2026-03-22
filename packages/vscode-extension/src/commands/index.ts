/** Command IDs — must match package.json contributes.commands */
export const COMMANDS = {
    OPEN_CHAT:      'vibe4fpga.openChat',
    SPEC2RTL:       'vibe4fpga.spec2rtl',
    CODE_REVIEW:    'vibe4fpga.codeReview',
    TIMING_FIX:     'vibe4fpga.timingFix',
    WAVEFORM_DEBUG: 'vibe4fpga.waveformDebug',
} as const;
