import type { WorkerSnapshot } from '@/modules/live/types/public';
import type { MutableJsonObject } from '@/types/shared';

const GAME_RESULTS = new Set<NonNullable<WorkerSnapshot['game_result']>>([
    'BLACK_WIN',
    'WHITE_WIN',
    'DRAW_BY_REPETITION',
    'ERROR',
    'BLACK_WIN_BY_DECLARATION',
    'WHITE_WIN_BY_DECLARATION',
    'DRAW_BY_MAX_PLIES',
    'INVALID',
    'BLACK_WIN_BY_FORFEIT',
    'WHITE_WIN_BY_FORFEIT',
    'DRAW_BY_IMPASSE',
    'PAUSED',
    'BLACK_WIN_BY_ILLEGAL_MOVE',
    'WHITE_WIN_BY_ILLEGAL_MOVE',
    'BLACK_WIN_BY_TIMEOUT',
    'WHITE_WIN_BY_TIMEOUT',
]);

export function ensureObject(value: unknown, context: string): MutableJsonObject {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`Invalid ${context}: expected object`);
    }
    return value as MutableJsonObject;
}

export function normalizeId(value: unknown, context: string): string | null {
    if (value == null) {
        return null;
    }
    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) {
            return null;
        }
        return trimmed;
    }
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) {
            throw new Error(`${context}: identifier must be finite`);
        }
        return String(value);
    }
    throw new Error(`${context}: invalid identifier value`);
}

export function normalizeOptionalString(value: unknown, context: string): string | null | undefined {
    if (value === undefined) {
        return undefined;
    }
    if (value === null) {
        return null;
    }
    if (typeof value !== 'string') {
        throw new Error(`${context}: expected string`);
    }
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
}

export function normalizeGameResult(value: unknown, context: string): WorkerSnapshot['game_result'] | undefined {
    const normalized = normalizeOptionalString(value, context);
    if (normalized === undefined) return undefined;
    if (normalized === null) return null;
    if (!GAME_RESULTS.has(normalized as NonNullable<WorkerSnapshot['game_result']>)) {
        throw new Error(`${context}: invalid game_result`);
    }
    return normalized as NonNullable<WorkerSnapshot['game_result']>;
}

export function normalizeRequiredString(value: unknown, context: string): string {
    const normalized = normalizeOptionalString(value, context);
    if (normalized == null) {
        throw new Error(`${context}: required string is missing`);
    }
    return normalized;
}

export function normalizeOptionalNumber(value: unknown, context: string): number | undefined {
    if (value === undefined || value === null) {
        return undefined;
    }
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        throw new Error(`${context}: expected finite number`);
    }
    return value;
}

export function normalizeOptionalNullableNumber(value: unknown, context: string): number | null | undefined {
    if (value === undefined) {
        return undefined;
    }
    if (value === null) {
        return null;
    }
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        throw new Error(`${context}: expected finite number or null`);
    }
    return value;
}

export function normalizeOptionalBoolean(value: unknown): boolean | undefined {
    if (value === undefined) {
        return undefined;
    }
    if (typeof value === 'boolean') {
        return value;
    }
    if (typeof value === 'number') {
        return value !== 0 && !Number.isNaN(value);
    }
    if (typeof value === 'string') {
        const lowered = value.trim().toLowerCase();
        if (!lowered) {
            return undefined;
        }
        if (['1', 'true', 'yes'].includes(lowered)) {
            return true;
        }
        if (['0', 'false', 'no'].includes(lowered)) {
            return false;
        }
    }
    return undefined;
}

export function normalizeStringArray(value: unknown, context: string): string[] {
    if (value === undefined || value === null) {
        return [];
    }
    if (!Array.isArray(value)) {
        throw new Error(`${context}: expected array of strings`);
    }
    return value.map((entry, index) => {
        const normalized = normalizeRequiredString(entry, `${context}[${index}]`);
        return normalized;
    });
}

export function normalizeOptionalStringArray(value: unknown, context: string): string[] | undefined {
    if (value === undefined) {
        return undefined;
    }
    const normalized = normalizeStringArray(value, context);
    return normalized.length ? normalized : [];
}

export function normalizeNumberArray(
    value: unknown,
    context: string,
    { allowNulls }: { allowNulls: boolean } = { allowNulls: true },
): (number | null)[] | undefined {
    if (value === undefined || value === null) {
        return undefined;
    }
    if (!Array.isArray(value)) {
        throw new Error(`${context}: expected array of numbers${allowNulls ? ' or nulls' : ''}`);
    }
    return value.map((entry, index) => {
        if (entry === null) {
            if (!allowNulls) {
                throw new Error(`${context}[${index}]: null is not allowed`);
            }
            return null;
        }
        if (typeof entry !== 'number' || !Number.isFinite(entry)) {
            throw new Error(`${context}[${index}]: expected finite number`);
        }
        return entry;
    });
}
