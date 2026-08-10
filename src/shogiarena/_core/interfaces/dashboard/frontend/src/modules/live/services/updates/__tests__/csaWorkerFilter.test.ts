import { describe, expect, it } from 'vitest';

import type { LiveUpdatesContext } from '@/modules/live/types/updates';

import { buildWsUrl, isCsaProfile, resolveSummaryTopic } from '../ws';

/**
 * The CSA profile discovers bridge runs while the page is open. A worker filter
 * pinned at connect time excludes any run that appears later, and worker-scoped
 * messages for excluded indices are dropped server-side — including the game
 * snapshot that would bring that run's card to life. So the CSA page must not
 * send one.
 *
 * Every other profile knows its workers before the page loads and keeps sending
 * the filter.
 */
function makeContext(profile: string, runtimeMode = 'tournament'): LiveUpdatesContext {
    const body = { dataset: profile ? { dashboardProfile: profile } : {} };
    return {
        owner: {
            location: { origin: 'http://localhost:7777' },
            document: { body },
        },
        core: { state: { runtimeMode } },
        state: { runtimeMode },
        getApiBase: () => 'http://localhost:7777',
    } as unknown as LiveUpdatesContext;
}

describe('buildWsUrl worker filter', () => {
    it('omits the worker filter for the csa profile', () => {
        const url = new URL(buildWsUrl(makeContext('csa'), new Set([0, 1])));
        expect(url.searchParams.get('workers')).toBeNull();
        expect(url.searchParams.get('protocol')).toBe('ws');
    });

    it('still sends the worker filter for other profiles', () => {
        const url = new URL(buildWsUrl(makeContext('tournament'), new Set([0, 1])));
        expect(url.searchParams.get('workers')).toBe('0,1');
    });

    it('sends no filter when there are no active workers, whatever the profile', () => {
        const url = new URL(buildWsUrl(makeContext('tournament'), new Set()));
        expect(url.searchParams.get('workers')).toBeNull();
    });
});

describe('isCsaProfile', () => {
    it('trusts the generated profile even though the runtime mode still says tournament', () => {
        // This is the real state of a CSA page while the socket is opening: the
        // mode is resolved from the summary, which has not arrived yet.
        expect(isCsaProfile(makeContext('csa', 'tournament'))).toBe(true);
    });

    it('accepts the runtime mode once it has caught up', () => {
        expect(isCsaProfile(makeContext('', 'csa'))).toBe(true);
    });

    it('does not claim other profiles', () => {
        expect(isCsaProfile(makeContext('tournament'))).toBe(false);
        expect(isCsaProfile(makeContext('spsa', 'spsa'))).toBe(false);
    });
});

describe('resolveSummaryTopic', () => {
    it('maps every mode to the topic the server actually publishes', () => {
        expect(resolveSummaryTopic('csa')).toBe('live.summary.snapshot.csa');
        expect(resolveSummaryTopic('spsa')).toBe('live.summary.snapshot.spsa');
        expect(resolveSummaryTopic('sprt')).toBe('live.summary.snapshot.sprt');
        expect(resolveSummaryTopic('match')).toBe('live.summary.snapshot.match');
        expect(resolveSummaryTopic('generate')).toBe('live.summary.snapshot.generate');
        expect(resolveSummaryTopic('tournament')).toBe('live.summary.snapshot.tournament');
    });

    it('falls back to tournament for an unknown mode', () => {
        expect(resolveSummaryTopic(undefined)).toBe('live.summary.snapshot.tournament');
        expect(resolveSummaryTopic('nonsense')).toBe('live.summary.snapshot.tournament');
    });
});
