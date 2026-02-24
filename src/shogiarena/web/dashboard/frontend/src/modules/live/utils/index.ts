import type { WorkerSnapshot } from '@/types/live';

const KIF_RESULT_LABELS: Record<number, string> = {
    0: '投了',
    1: '投了',
    2: '千日手',
    3: '反則負け',
    4: '宣言勝ち',
    5: '宣言勝ち',
    6: '最大手数',
    7: '中断',
    8: '反則負け',
    9: '反則負け',
    10: '持将棋',
    11: '中断',
    12: '反則負け',
    13: '反則負け',
    15: 'エラー (初期設定)',
    16: '時間切れ負け',
    17: '時間切れ負け',
    18: 'エラー (リソース制約)',
    19: 'エラー (未知)',
};

export const DEFAULT_INITIAL_SFEN = 'startpos';
export const INCREMENT_PLACEHOLDER = '\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0';

type ResultContext = {
    end_reason?: unknown;
    meta?: unknown;
    [key: string]: unknown;
};

type DrawCode6Reason = 'max_plies' | 'jishogi' | null;

function readDrawReasonHints(context: ResultContext | null | undefined): string[] {
    if (!context || typeof context !== 'object') return [];
    const hints: string[] = [];
    const pushIfString = (value: unknown): void => {
        if (typeof value === 'string' && value.trim()) {
            hints.push(value.trim().toLowerCase());
        }
    };

    pushIfString(context.end_reason);
    pushIfString(context.endReason);

    const meta = context.meta;
    if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
        const metaObj = meta as Record<string, unknown>;
        pushIfString(metaObj.reason);
        pushIfString(metaObj.end_reason);
        pushIfString(metaObj.endReason);
        pushIfString(metaObj.draw_reason);
        pushIfString(metaObj.drawReason);
        pushIfString(metaObj.result_detail);
        pushIfString(metaObj.resultDetail);
    }
    return hints;
}

function inferDrawCode6Reason(context: ResultContext | null | undefined): DrawCode6Reason {
    const hints = readDrawReasonHints(context);
    if (hints.length === 0) return null;
    const joined = hints.join(' ');
    if (/jishogi|impasse|持将棋/.test(joined)) return 'jishogi';
    if (/max|ply|limit|move|最大手数/.test(joined)) return 'max_plies';
    return null;
}

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

export function resultCodeToKifJP(code: unknown, context?: ResultContext | null): string {
    const numeric = typeof code === 'string' ? Number.parseInt(code, 10) : Number(code);
    if (!Number.isFinite(numeric)) {
        return '結果不明';
    }
    if (numeric === 6) {
        const reason = inferDrawCode6Reason(context);
        if (reason === 'jishogi') return '持将棋';
        if (reason === 'max_plies') return '最大手数';
        return '最大手数';
    }
    return KIF_RESULT_LABELS[numeric] ?? '結果不明';
}
