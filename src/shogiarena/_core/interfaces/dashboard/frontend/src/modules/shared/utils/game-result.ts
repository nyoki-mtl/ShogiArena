const GAME_RESULT_LABELS = {
    BLACK_WIN: '投了',
    WHITE_WIN: '投了',
    DRAW_BY_REPETITION: '千日手',
    ERROR: 'エラー',
    BLACK_WIN_BY_DECLARATION: '宣言勝ち',
    WHITE_WIN_BY_DECLARATION: '宣言勝ち',
    DRAW_BY_MAX_PLIES: '最大手数',
    INVALID: '無効対局',
    BLACK_WIN_BY_FORFEIT: '反則負け',
    WHITE_WIN_BY_FORFEIT: '反則負け',
    DRAW_BY_IMPASSE: '持将棋',
    PAUSED: '中断',
    BLACK_WIN_BY_ILLEGAL_MOVE: '反則負け',
    WHITE_WIN_BY_ILLEGAL_MOVE: '反則負け',
    BLACK_WIN_BY_TIMEOUT: '時間切れ負け',
    WHITE_WIN_BY_TIMEOUT: '時間切れ負け',
} as const;

const BLACK_WIN_RESULTS = new Set([
    'BLACK_WIN',
    'BLACK_WIN_BY_DECLARATION',
    'BLACK_WIN_BY_FORFEIT',
    'BLACK_WIN_BY_ILLEGAL_MOVE',
    'BLACK_WIN_BY_TIMEOUT',
]);

const WHITE_WIN_RESULTS = new Set([
    'WHITE_WIN',
    'WHITE_WIN_BY_DECLARATION',
    'WHITE_WIN_BY_FORFEIT',
    'WHITE_WIN_BY_ILLEGAL_MOVE',
    'WHITE_WIN_BY_TIMEOUT',
]);

const DRAW_RESULTS = new Set(['DRAW_BY_REPETITION', 'DRAW_BY_MAX_PLIES', 'DRAW_BY_IMPASSE']);
const ERROR_RESULTS = new Set(['ERROR', 'INVALID']);

const GAME_RESULT_DETAIL_KEYS: Partial<Record<keyof typeof GAME_RESULT_LABELS, string>> = {
    BLACK_WIN_BY_DECLARATION: 'declaration',
    WHITE_WIN_BY_DECLARATION: 'declaration',
    BLACK_WIN_BY_FORFEIT: 'forfeit',
    WHITE_WIN_BY_FORFEIT: 'forfeit',
    BLACK_WIN_BY_ILLEGAL_MOVE: 'illegal move',
    WHITE_WIN_BY_ILLEGAL_MOVE: 'illegal move',
    BLACK_WIN_BY_TIMEOUT: 'timeout',
    WHITE_WIN_BY_TIMEOUT: 'timeout',
    DRAW_BY_REPETITION: 'repetition',
    DRAW_BY_MAX_PLIES: 'max plies',
    DRAW_BY_IMPASSE: 'impasse',
    ERROR: 'error',
    INVALID: 'invalid',
    PAUSED: 'paused',
};

const GAME_RESULT_ABBREVIATIONS: Partial<Record<keyof typeof GAME_RESULT_LABELS, string>> = {
    BLACK_WIN_BY_DECLARATION: 'D',
    WHITE_WIN_BY_DECLARATION: 'D',
    BLACK_WIN_BY_FORFEIT: 'F',
    WHITE_WIN_BY_FORFEIT: 'F',
    BLACK_WIN_BY_ILLEGAL_MOVE: 'I',
    WHITE_WIN_BY_ILLEGAL_MOVE: 'I',
    BLACK_WIN_BY_TIMEOUT: 'T',
    WHITE_WIN_BY_TIMEOUT: 'T',
    DRAW_BY_REPETITION: 'R',
    DRAW_BY_MAX_PLIES: 'M',
    DRAW_BY_IMPASSE: 'I',
    ERROR: 'ERR',
    INVALID: 'INV',
    PAUSED: 'P',
};

export type GameResultCategory = 0 | 1 | 2 | 3 | null;
export type GameResultName = keyof typeof GAME_RESULT_LABELS;

export function isGameResultName(value: unknown): value is GameResultName {
    return typeof value === 'string' && Object.hasOwn(GAME_RESULT_LABELS, value);
}

export function gameResultLabel(gameResult: unknown): string | null {
    if (!isGameResultName(gameResult)) {
        return null;
    }
    return GAME_RESULT_LABELS[gameResult] ?? null;
}

export function gameResultDetailKey(gameResult: unknown): string | null {
    if (!isGameResultName(gameResult)) {
        return null;
    }
    return GAME_RESULT_DETAIL_KEYS[gameResult] ?? null;
}

export function gameResultAbbreviation(gameResult: unknown): string | null {
    if (!isGameResultName(gameResult)) {
        return null;
    }
    return GAME_RESULT_ABBREVIATIONS[gameResult] ?? null;
}

export function gameResultCategory(gameResult: unknown): GameResultCategory {
    if (!isGameResultName(gameResult)) {
        return null;
    }
    if (BLACK_WIN_RESULTS.has(gameResult)) return 0;
    if (WHITE_WIN_RESULTS.has(gameResult)) return 1;
    if (DRAW_RESULTS.has(gameResult)) return 2;
    if (gameResult === 'PAUSED' || ERROR_RESULTS.has(gameResult)) return 3;
    return null;
}

export function gameResultWinner(gameResult: unknown): 'black' | 'white' | 'draw' | 'error' | 'unknown' {
    const category = gameResultCategory(gameResult);
    if (category === 0) return 'black';
    if (category === 1) return 'white';
    if (category === 2) return 'draw';
    if (category === 3) return 'error';
    return 'unknown';
}

export function gameResultScores(gameResult: unknown): { black: number | null; white: number | null } {
    const category = gameResultCategory(gameResult);
    if (category === 0) {
        return { black: 1, white: 0 };
    }
    if (category === 1) {
        return { black: 0, white: 1 };
    }
    if (category === 2) {
        return { black: 0.5, white: 0.5 };
    }
    return { black: null, white: null };
}

export function gameResultOutcomeKind(
    gameResult: unknown,
): 'black-win' | 'white-win' | 'draw' | 'paused' | 'error' | null {
    if (!isGameResultName(gameResult)) {
        return null;
    }
    if (BLACK_WIN_RESULTS.has(gameResult)) return 'black-win';
    if (WHITE_WIN_RESULTS.has(gameResult)) return 'white-win';
    if (DRAW_RESULTS.has(gameResult)) return 'draw';
    if (gameResult === 'PAUSED') return 'paused';
    if (ERROR_RESULTS.has(gameResult)) return 'error';
    return null;
}
