import { afterEach, describe, expect, it } from 'vitest';

import type { NormalizedGameRow } from '@/modules/games/types';
import { installGamesRender } from './render';

afterEach(() => {
    delete document.body.dataset.dashboardProfile;
});

describe('CSA Games rows', () => {
    it('keeps player names inert and marks our side with an Ours pill', () => {
        document.body.dataset.dashboardProfile = 'csa';
        const row = installGamesRender().buildRow({
            game_id: 'csa_internal',
            server_game_id: 'g1',
            status: 'completed',
            round_index: null,
            order_index: 1,
            display_order: 1,
            black: 'our-engine',
            white: 'opponent',
            black_instance: 'Ours',
            black_instance_kind: 'csa-owned',
            white_instance: null,
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
            game_result: 'BLACK_WIN',
            result_detail: '',
            result_abbr: '',
            result_label: '',
            total_plies: 42,
            started_at: '2026-08-08T12:00:00+00:00',
            ended_at: '2026-08-08T12:10:00+00:00',
        } satisfies NormalizedGameRow);

        expect(row.querySelectorAll('.games-engine-link')).toHaveLength(0);
        expect(row.querySelectorAll('.games-opening-badge')).toHaveLength(0);
        expect(row.querySelector('.csa-ownership-pill')?.textContent).toBe('Ours');
        expect(row.querySelector('.games-game-link')?.textContent).toBe('g1');
        expect((row.querySelector('.games-game-link') as HTMLElement)?.dataset.gameId).toBe('csa_internal');
        expect(row.querySelector('.games-game-link')?.getAttribute('title')).toBe('g1');
        expect(row.querySelector('.games-engine-label')?.getAttribute('title')).toBe('our-engine');
    });
});
