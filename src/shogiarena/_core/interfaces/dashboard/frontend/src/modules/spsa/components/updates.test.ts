import { beforeEach, describe, expect, it } from 'vitest';
import { applyUpdatesResponse, getState } from '../state';
import { renderUpdatesTable } from './updates';

/**
 * ここで使う payload は、実 run の `/api/spsa/updates` が返した内容をそのまま写したもの。
 *
 * 重要なのは `games_completed` が `null` である点。バックエンドは一覧エントリに
 * そのキーを載せていない。過去に「まだ結果が無いか」の判定を `games_completed` で
 * 行ったため、勝敗が届いていても W-D-L 列が恒久的に空欄になる不具合を出した。
 */
const REAL_UPDATE_ENTRIES = [
    {
        update_idx: 1,
        wins: 2,
        losses: 2,
        draws: 0,
        games_completed: null,
        total_games: 4,
        started_at: 1785844079041,
        ended_at: 1785844084094,
        delta_norm: 0,
    },
    {
        update_idx: 2,
        wins: 3,
        losses: 1,
        draws: 0,
        games_completed: null,
        total_games: 4,
        started_at: 1785844084122,
        ended_at: 1785844089347,
        delta_norm: 16.393895221541854,
    },
    {
        update_idx: 3,
        wins: 4,
        losses: 0,
        draws: 0,
        games_completed: null,
        total_games: 4,
        started_at: 1785844089372,
        ended_at: 1785844093347,
        delta_norm: 16.979178199101085,
    },
];

beforeEach(() => {
    document.body.innerHTML = `
        <table><tbody id="updatesTableBody"></tbody></table>
    `;
});

function renderRealEntries(entries: unknown[]): string {
    // `/api/spsa/updates` の応答そのものの形で流し込む。
    applyUpdatesResponse({
        updates: entries,
        total: entries.length,
        limit: 50,
        offset: 0,
        has_more: false,
    } as never);
    renderUpdatesTable(getState());
    return document.getElementById('updatesTableBody')?.innerHTML ?? '';
}

describe('SPSA updates table', () => {
    it('fills the W-D-L cell from a real backend payload', () => {
        const html = renderRealEntries(REAL_UPDATE_ENTRIES);

        expect(html).toContain('2-0-2');
        expect(html).toContain('3-0-1');
        expect(html).toContain('4-0-0');
    });

    it('does not depend on games_completed, which the list endpoint omits', () => {
        const html = renderRealEntries(REAL_UPDATE_ENTRIES);
        const wdlCells = html.match(/class="update-wdl-cell">([^<]*)</g) ?? [];

        expect(wdlCells).toHaveLength(REAL_UPDATE_ENTRIES.length);
        expect(wdlCells.every((cell) => !cell.includes('—'))).toBe(true);
    });

    it('shows a placeholder only while an update has no finished games', () => {
        const html = renderRealEntries([
            { update_idx: 9, wins: 0, losses: 0, draws: 0, games_completed: null, total_games: 4 },
        ]);

        expect(html).toContain('—');
    });

    it('renders the start and finish timestamps the backend now provides', () => {
        const html = renderRealEntries(REAL_UPDATE_ENTRIES);

        // 具体的な書式はロケール依存なので、プレースホルダのままでないことだけ見る。
        const cells = html.match(/<td>[^<]*<\/td>/g) ?? [];
        const placeholders = cells.filter((cell) => cell === '<td>-</td>');
        expect(placeholders).toHaveLength(0);
    });
});
