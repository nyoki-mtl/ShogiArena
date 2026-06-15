import { describe, expect, it } from 'vitest';

import type { NormalizedGameRow } from '@/modules/games/types';
import { buildResultViewModel } from '@/modules/games/viewmodel-result';

function createNormalizedGameRow(overrides: Partial<NormalizedGameRow> = {}): NormalizedGameRow {
    return {
        game_id: 'game-1',
        status: 'completed',
        round_index: 0,
        order_index: 0,
        display_order: 0,
        black: 'Black',
        white: 'White',
        black_instance: null,
        white_instance: null,
        black_instance_kind: null,
        white_instance_kind: null,
        initial_sfen: 'startpos',
        instances: [],
        assigned_instance: null,
        assigned_override: null,
        assigned_override_black: null,
        assigned_override_white: null,
        assigned_mode: 'auto',
        resolved_instance_black: null,
        resolved_instance_white: null,
        should_require_install: false,
        result_abbr: '',
        result_label: '',
        result_detail: '',
        game_result: null,
        total_plies: null,
        started_at: null,
        ended_at: null,
        ...overrides,
    };
}

describe('buildResultViewModel', () => {
    it('derives max-plies draw badge content from game_result when legacy fields are absent', () => {
        const result = buildResultViewModel(
            createNormalizedGameRow({
                game_result: 'DRAW_BY_MAX_PLIES',
            }),
            'completed',
        );

        expect(result.variant).toBe('draw');
        expect(result.shape).toBe('triangle');
        expect(result.detailKey).toBe('max plies');
        expect(result.display).toBe('M');
        expect(result.tooltip).toContain('Max Moves');
    });
});
