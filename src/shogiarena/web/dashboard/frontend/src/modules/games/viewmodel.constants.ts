const NBSP = '\u00A0';

export const MOVES_PLACEHOLDER = NBSP.repeat(3);
export const TIME_PLACEHOLDER = NBSP.repeat(20);

export const RESULT_DETAIL_LABELS: Readonly<Record<string, string>> = Object.freeze({
    declaration: 'Declaration',
    forfeit: 'Forfeit',
    'illegal move': 'Illegal Move',
    timeout: 'Timeout',
    repetition: 'Rep',
    'max plies': 'Max Moves',
    error: 'Error',
    invalid: 'Invalid',
    paused: 'Paused',
});

export const RESULT_DETAIL_SUFFIXES: Readonly<Record<string, string>> = Object.freeze({
    declaration: 'by Declaration',
    forfeit: 'by Forfeit',
    'illegal move': 'by Illegal Move',
    timeout: 'by Timeout',
    repetition: 'by Repetition',
    'max plies': 'by Max Moves',
});

export const RESULT_OUTCOME_ORDER = Object.freeze([
    'black-win',
    'white-win',
    'draw',
    'paused',
    'running',
    'pending',
    'cancelled',
    'error',
] as const);

export const RESULT_DETAIL_ORDER = Object.freeze([
    '',
    'declaration',
    'forfeit',
    'illegal move',
    'timeout',
    'repetition',
    'max plies',
    'error',
    'invalid',
    'paused',
] as const);

export const RESULT_OUTCOME_INDEX = new Map<string, number>(RESULT_OUTCOME_ORDER.map((value, index) => [value, index]));
export const RESULT_DETAIL_INDEX = new Map<string, number>(RESULT_DETAIL_ORDER.map((value, index) => [value, index]));

export const RESULT_DETAIL_SYMBOLS = Object.freeze({
    declaration: 'D',
    timeout: 'T',
    forfeit: 'F',
    'illegal move': 'I',
} as const satisfies Record<string, string>);
