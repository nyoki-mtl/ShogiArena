import { describe, expect, it, vi } from 'vitest';

import { applyTabConfiguration, getModeConfig } from '@/modules/shared/services/runtime-mode';
import type { DashboardRuntimeMode } from '@/types/dashboard';
import type { DashboardTabId } from '@/types/globals';

const ALL_MODES: DashboardRuntimeMode[] = ['tournament', 'match', 'sprt', 'spsa', 'generate', 'csa', 'unknown'];

function applyAndCollect(mode: DashboardRuntimeMode): Map<DashboardTabId, boolean> {
    const calls = new Map<DashboardTabId, boolean>();
    applyTabConfiguration(
        {
            setActive: vi.fn(),
            setVisibility: (tabId: DashboardTabId, visible: boolean) => {
                calls.set(tabId, visible);
            },
            setOrder: vi.fn(),
            getActive: () => 'live' as DashboardTabId,
        },
        mode,
    );
    return calls;
}

/**
 * The CSA profile owns a tab no other dashboard should ever show.
 *
 * The danger is asymmetric to the bugs that prompted it. Those were branches
 * that did not know about `csa` and silently degraded it; the risk when adding a
 * CSA-only tab is the mirror image — markup that leaks into tournament or SPSA.
 * `applyTabConfiguration` only touches ids listed in its own `allTabs` array, so
 * an id omitted there is hidden by nobody.
 */
describe('tab visibility across modes', () => {
    it('decides every tab explicitly, in every mode', () => {
        // If a tab id is missing from `allTabs` it is never passed to
        // setVisibility, and whichever profile ships its markup keeps it.
        const reference = applyAndCollect('tournament');
        for (const mode of ALL_MODES) {
            const calls = applyAndCollect(mode);
            expect([...calls.keys()].sort()).toEqual([...reference.keys()].sort());
        }
    });

    it('hides the csa tab in every mode except csa', () => {
        for (const mode of ALL_MODES) {
            const calls = applyAndCollect(mode);
            expect(calls.get('csa')).toBe(mode === 'csa');
        }
    });

    it('gives the csa profile its three responsibility-based tabs and nothing else', () => {
        expect(getModeConfig('csa').visibleTabs).toEqual(['live', 'games', 'csa']);
        const calls = applyAndCollect('csa');
        const visible = [...calls.entries()].filter(([, shown]) => shown).map(([tabId]) => tabId);
        expect(visible.sort()).toEqual(['csa', 'games', 'live']);
    });

    it('leaves the other profiles untouched', () => {
        expect(getModeConfig('tournament').visibleTabs).toEqual([
            'live',
            'tournament',
            'openings',
            'rules',
            'engines',
            'instances',
            'games',
            'book',
        ]);
        expect(getModeConfig('spsa').visibleTabs).toEqual(['live', 'spsa', 'rules', 'engines', 'instances']);
    });
});
