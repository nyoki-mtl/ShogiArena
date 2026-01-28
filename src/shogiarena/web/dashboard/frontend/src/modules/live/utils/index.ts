import type { WorkerSnapshot } from '@/types/live';

const KIF_RESULT_LABELS: Record<number, string> = {
    0: '投了',
    1: '投了',
    2: '千日手',
    3: '反則負け',
    4: '宣言勝ち',
    5: '宣言勝ち',
    6: '持将棋',
    7: '中断',
    8: '反則負け',
    9: '反則負け',
    10: '中断',
    11: 'ルール違反',
    12: '反則負け',
    13: '反則負け',
    14: 'エラー (通信)',
    15: 'エラー (初期設定)',
    16: '時間切れ負け',
    17: '時間切れ負け',
    18: 'エラー (リソース制約)',
    19: 'エラー (未知)',
};

export const DEFAULT_INITIAL_SFEN = 'startpos';
export const INCREMENT_PLACEHOLDER = '\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0';

export function createEmptyWorkerSnapshot(): WorkerSnapshot {
    return {
        game_id: null,
        initial_sfen: DEFAULT_INITIAL_SFEN,
        sfen: null,
        black_name: null,
        white_name: null,
        moves: [],
        ki2_moves: [],
        eval_black: [],
        eval_white: [],
        nodes_values: [],
        depth_values: [],
        seldepth_values: [],
        move_times_ms: [],
        wall_times_ms: [],
        latency_deltas_ms: [],
        latency_alerts: [],
        currentPly: 0,
    };
}

export function resultCodeToKifJP(code: unknown): string {
    const numeric = typeof code === 'string' ? Number.parseInt(code, 10) : Number(code);
    if (!Number.isFinite(numeric)) {
        return '結果不明';
    }
    return KIF_RESULT_LABELS[numeric] ?? '結果不明';
}
