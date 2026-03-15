import { describe, expect, it } from 'vitest';

import type { NormalizedTournamentSummary, TournamentSummary } from '@/modules/tournament/types';
import { buildTournamentStoreSummaryPayload } from './summaryStorePayload';

describe('buildTournamentStoreSummaryPayload', () => {
    it('preserves liveView and games from the raw summary while replacing normalized engine fields', () => {
        const rawSummary: TournamentSummary = {
            liveView: {
                mode: 'tournament',
                progress: {
                    kind: 'games',
                    completed: 3,
                    total: 10,
                },
            },
            games: {
                completed: 3,
                total: 10,
                cancelled: 0,
            },
            engines: ['old-a'],
            engineMeta: {
                'old-a': { name: 'old-a' },
            },
            engineTimeControls: {
                'old-a': '10+0',
            },
            engineInstances: {
                'old-a': 'inst-old',
            },
            defaultTimeControl: '10+0',
        };
        const normalized: NormalizedTournamentSummary = {
            engines: ['new-a', 'new-b'],
            engineMeta: {
                'new-a': { name: 'new-a', raw: {} },
                'new-b': { name: 'new-b', raw: {} },
            },
            engineTimeControls: {
                'new-a': '30+0',
            },
            engineInstances: {
                'new-a': 'inst-1',
                'new-b': null,
            },
            defaultTimeControl: '30+0',
        } as unknown as NormalizedTournamentSummary;

        const payload = buildTournamentStoreSummaryPayload(rawSummary, normalized);

        expect(payload.liveView).toEqual(rawSummary.liveView);
        expect(payload.games).toEqual(rawSummary.games);
        expect(payload.engines).toEqual(['new-a', 'new-b']);
        expect(payload.engineMeta).toEqual(normalized.engineMeta);
        expect(payload.engineTimeControls).toEqual(normalized.engineTimeControls);
        expect(payload.engineInstances).toEqual(normalized.engineInstances);
        expect(payload.defaultTimeControl).toBe('30+0');
    });
});
