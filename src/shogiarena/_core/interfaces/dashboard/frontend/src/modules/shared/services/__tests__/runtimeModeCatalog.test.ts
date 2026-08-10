import { describe, expect, it } from 'vitest';

import { DASHBOARD_RUNTIME_MODES, isDashboardRuntimeMode } from '@/modules/shared/services/runtime-mode-catalog';
import { getModeConfig, normalizeRuntimeMode } from '@/modules/shared/services/runtime-mode';

/**
 * The catalogue is the single place a mode is declared.
 *
 * These tests exist because four separate hand-written branches once degraded a
 * valid mode in silence. The rule they encode: a mode in the catalogue is a mode
 * everywhere, and only genuinely unrecognised input becomes `unknown`.
 */
describe('runtime mode catalogue', () => {
    it('accepts every catalogued mode through the normalizer', () => {
        for (const mode of DASHBOARD_RUNTIME_MODES) {
            expect(normalizeRuntimeMode(mode)).toBe(mode);
        }
    });

    it('answers for every catalogued mode in the config table', () => {
        // `Record<DashboardRuntimeMode, …>` makes this a compile-time guarantee;
        // the runtime check catches a table that was widened with `Partial` or an
        // index signature, which would put the guarantee back in human hands.
        for (const mode of DASHBOARD_RUNTIME_MODES) {
            const config = getModeConfig(mode);
            expect(config.mode).toBe(mode);
            expect(Array.isArray(config.visibleTabs)).toBe(true);
            expect(typeof config.showProgressBar).toBe('boolean');
        }
    });

    it('still folds tournament aliases the server may send', () => {
        expect(normalizeRuntimeMode('gauntlet')).toBe('tournament');
        expect(normalizeRuntimeMode('roundrobin')).toBe('tournament');
    });

    it('is forgiving about case and whitespace', () => {
        expect(normalizeRuntimeMode('  CSA ')).toBe('csa');
    });

    it('calls genuinely unrecognised input unknown, and nothing else', () => {
        expect(normalizeRuntimeMode('bogus')).toBe('unknown');
        expect(normalizeRuntimeMode(null)).toBe('unknown');
        expect(normalizeRuntimeMode(42)).toBe('unknown');
        expect(isDashboardRuntimeMode('bogus')).toBe(false);
        expect(isDashboardRuntimeMode('csa')).toBe(true);
    });
});
