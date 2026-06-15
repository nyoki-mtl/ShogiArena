import { describe, expect, it } from 'vitest';

import type { NormalizedTournamentSummary, TournamentSummary } from '@/modules/tournament/types';
import { buildTournamentStoreSummaryPayload } from './summary-store-payload';

describe('buildTournamentStoreSummaryPayload', () => {
    it('preserves liveView and games from the raw summary while replacing normalized engine fields', () => {
        const rawSummary: TournamentSummary = {
            live_view: {
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
            engine_meta: {
                'old-a': { name: 'old-a' },
            },
            engine_time_controls: {
                'old-a': '10+0',
            },
            engine_instances: {
                'old-a': 'inst-old',
            },
            default_time_control: '10+0',
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

        expect(payload.live_view).toEqual(rawSummary.live_view);
        expect(payload.games).toEqual(rawSummary.games);
        expect(payload.engines).toEqual(['new-a', 'new-b']);
        expect(payload.engine_meta).toEqual(normalized.engineMeta);
        expect(payload.engine_time_controls).toEqual(normalized.engineTimeControls);
        expect(payload.engine_instances).toEqual(normalized.engineInstances);
        expect(payload.default_time_control).toBe('30+0');
    });
});
