import { describe, expect, it } from 'vitest';

import { normalizeLiveGameRecord } from '@/modules/live/services/updates/normalizers/live-game';
import type { LiveGameRecord } from '@/modules/live/types/public';

import golden from './fixtures/live-game-golden.json';

/**
 * Cross-language contract check.
 *
 * The fixture is emitted by the Python publisher (`tests/unit/test_csa_live_publisher.py`)
 * and read here unmodified. If the two sides drift, one of these two tests fails
 * instead of the dashboard silently rendering an empty card.
 */
describe('CSA worker snapshot -> live game record', () => {
    const record = golden as LiveGameRecord;

    it('accepts persisted game player field names', () => {
        const normalized = normalizeLiveGameRecord({
            game_id: 'persisted-csa-game',
            black_player: 'ours',
            white_player: 'opponent',
            moves: ['7g7f'],
        });

        expect(normalized.black_name).toBe('ours');
        expect(normalized.white_name).toBe('opponent');
    });

    it('passes the live card normalizer', () => {
        expect(() => normalizeLiveGameRecord(record, 'csa_golden')).not.toThrow();
    });

    it('keeps every move and its reading', () => {
        const normalized = normalizeLiveGameRecord(record, 'csa_golden');
        expect(normalized.moves.length).toBeGreaterThan(0);
        expect(normalized.ki2_moves).toHaveLength(normalized.moves.length);
        expect(normalized.current_ply).toBe(normalized.moves.length);
    });

    it('keeps the per-ply series aligned with the move list', () => {
        const normalized = normalizeLiveGameRecord(record, 'csa_golden');
        const plies = normalized.moves.length;
        for (const series of [
            normalized.eval_black,
            normalized.eval_white,
            normalized.nodes_values,
            normalized.depth_values,
            normalized.move_times_ms,
        ]) {
            expect(series).toHaveLength(plies);
        }
    });

    it('fills exactly one side of the evaluation graph', () => {
        const normalized = normalizeLiveGameRecord(record, 'csa_golden');
        const blackHasValues = normalized.eval_black.some((value) => value !== null);
        const whiteHasValues = normalized.eval_white.some((value) => value !== null);
        expect(blackHasValues).not.toBe(whiteHasValues);
    });

    it('reports a result the dashboard vocabulary accepts', () => {
        const normalized = normalizeLiveGameRecord(record, 'csa_golden');
        expect(normalized.game_result).toBe('BLACK_WIN');
    });
});
