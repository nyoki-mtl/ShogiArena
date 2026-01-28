import { describe, expect, it } from 'vitest';

import {
    normalizeTournamentGame,
    normalizeTournamentGamesList,
    normalizeTournamentSummary,
} from '@/modules/tournament/services/normalizers';
import { deriveProgressDisplay } from '@/modules/shared/components/progress';

describe('tournament game normalizers', () => {
    it('normalizes individual game payloads with mixed field names', () => {
        const date = new Date('2025-10-05T12:34:56Z');
        const normalized = normalizeTournamentGame({
            game_id: 'game-001',
            initial_sfen: 'startpos',
            black_player: '  Black Engine  ',
            white_player: 'White Engine',
            result_code: '0',
            total_plies: '120',
            start_time: date,
            adjudication: { reason: 'max_moves' },
        });

        expect(normalized).not.toBeNull();
        expect(normalized?.gameId).toBe('game-001');
        expect(normalized?.initialSfen).toBe('startpos');
        expect(normalized?.players.black.name).toBe('Black Engine');
        expect(normalized?.players.white.name).toBe('White Engine');
        expect(normalized?.resultCode).toBe(0);
        expect(normalized?.resultCategory).toBe(0);
        expect(normalized?.winner).toBe('black');
        expect(normalized?.scores).toEqual({ black: 1, white: 0 });
        expect(normalized?.totalPlies).toBe(120);
        expect(normalized?.startTime).toBe(date.toISOString());
        expect(normalized?.adjudicationReason).toBe('max_moves');
    });

    it('treats null result_code as pending', () => {
        const normalized = normalizeTournamentGame({
            game_id: 'pending-001',
            initial_sfen: 'startpos',
            black_player: 'Engine A',
            white_player: 'Engine B',
            result_code: null,
        });

        expect(normalized).not.toBeNull();
        expect(normalized?.resultCode).toBeNull();
        expect(normalized?.resultCategory).toBeNull();
        expect(normalized?.winner).toBe('unknown');
        expect(normalized?.scores).toEqual({ black: null, white: null });
    });

    it('produces normalized game list compatible with pentanomial scoring', () => {
        const rawGames = [
            {
                game_id: 'pair-1-a',
                initial_sfen: 'startpos',
                black_player: 'Engine A',
                white_player: 'Engine B',
                result_code: 0,
            },
            {
                game_id: 'pair-1-b',
                initial_sfen: 'startpos',
                black_player: 'Engine B',
                white_player: 'Engine A',
                result_code: 1,
            },
            {
                game_id: 'pair-2-a',
                initial_sfen: 'startpos',
                black_player: 'Engine A',
                white_player: 'Engine B',
                result_code: 2,
            },
            {
                game_id: 'pair-2-b',
                initial_sfen: 'startpos',
                black_player: 'Engine B',
                white_player: 'Engine A',
                result_code: 2,
            },
        ];

        const games = normalizeTournamentGamesList(rawGames);
        expect(games).toHaveLength(4);

        const gAB = games.filter(
            (game) => game.players.black.name === 'Engine A' && game.players.white.name === 'Engine B',
        );
        const gBA = games.filter(
            (game) => game.players.black.name === 'Engine B' && game.players.white.name === 'Engine A',
        );

        const limit = Math.min(gAB.length, gBA.length);
        const bins: Record<string, number> = { '2.0': 0, '1.5': 0, '1.0': 0, '0.5': 0, '0.0': 0 };

        for (let i = 0; i < limit; i += 1) {
            let score = 0;
            const cat1 = gAB[i].resultCategory;
            if (cat1 === 0) score += 1.0;
            else if (cat1 === 2) score += 0.5;
            const cat2 = gBA[i].resultCategory;
            if (cat2 === 1) score += 1.0;
            else if (cat2 === 2) score += 0.5;
            bins[score.toFixed(1)] += 1;
        }

        expect(bins).toMatchObject({ '2.0': 1, '1.0': 1 });
    });

    it('normalizes tournament summary meta and pair results', () => {
        const normalized = normalizeTournamentSummary({
            engines: ['Engine A', 'Engine B'],
            engineStats: {
                'Engine A': { wins: 3, draws: 1, losses: 2, rating: 1520 },
                'Engine B': { wins: 2, draws: 1, losses: 3, rating: 1480 },
            },
            pairResults: {
                'Engine A_vs_Engine B': {
                    'Engine A_wins': 3,
                    'Engine B_wins': 2,
                    draws: 1,
                },
            },
            enginesMeta: [
                {
                    name: 'Engine A',
                    engine_path: '/bin/engine_a',
                    merged_options: { Threads: 4 },
                },
            ],
        });

        expect(Object.keys(normalized.engineMeta)).toEqual(expect.arrayContaining(['Engine A', 'Engine B']));
        const metaA = normalized.engineMeta['Engine A'];
        expect(metaA.enginePath).toBe('/bin/engine_a');
        expect(metaA.merged_options).toEqual({ Threads: 4 });
        expect(metaA.option_sources).toEqual({});

        const metaB = normalized.engineMeta['Engine B'];
        expect(metaB.enginePath).toBeNull();
        expect(metaB.merged_options).toEqual({});
        expect(metaB.option_sources).toEqual({});
        expect(metaB.raw).toMatchObject({ name: 'Engine B' });

        const pairA = normalized.pairResults['Engine A'];
        expect(pairA?.['Engine B']).toEqual({ wins: 3, losses: 2, draws: 1 });
        const pairB = normalized.pairResults['Engine B'];
        expect(pairB?.['Engine A']).toEqual({ wins: 2, losses: 3, draws: 1 });

        expect(normalized.engineInstances).toMatchObject({ 'Engine A': null, 'Engine B': null });
        expect(normalized.btd).toBeNull();
        expect(normalized.sprt).toBeNull();
        expect(normalized.tournamentType).toBeNull();
        expect(normalized.runDir).toBeNull();
    });

    it('tolerates malformed enginesMeta entries', () => {
        const normalized = normalizeTournamentSummary({
            engines: ['Engine A', 'Engine B'],
            enginesMeta: [
                { name: null, engine_path: '/bin/bad-null' },
                { name: { bad: 'object' }, engine_path: '/bin/bad-object' },
                123,
                {
                    name: 'Engine A',
                    engine_path: '/bin/engine_a',
                    merged_options: { Threads: 4 },
                },
            ],
        });

        expect(Object.keys(normalized.engineMeta)).toEqual(expect.arrayContaining(['Engine A', 'Engine B']));
        expect(normalized.engineMeta['Engine A'].enginePath).toBe('/bin/engine_a');
        expect(normalized.engineMeta['Engine A'].merged_options).toEqual({ Threads: 4 });
    });

    it('derives active total games after cancellations', () => {
        const normalized = normalizeTournamentSummary({
            games: {
                completed: 12,
                total: 96,
                cancelled: 4,
            },
        });

        expect(normalized.totalGames).toBe(96);
        expect(normalized.originalTotalGames).toBe(100);
        expect(normalized.cancelledGames).toBe(4);
        expect(normalized.tournamentFinished).toBe(false);
    });

    it('annotates progress display with cancellation count', () => {
        const model = deriveProgressDisplay({
            completed: 18,
            total: 18,
            indicator: 'finished',
            isFinal: true,
            cancelled: 2,
        });

        expect(model.displayText).toBe('18 / 18 (completed, 2 cancelled)');
    });
});
