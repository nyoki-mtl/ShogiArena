import { formatDurationMs } from '@/modules/shared/utils/format';
import type { ParsedTimeControlSpec } from '@/modules/tournament/types';

export function parseTimeControlSpec(spec: string): ParsedTimeControlSpec {
    if (typeof spec !== 'string') {
        throw new Error('Time control spec must be a string');
    }
    const normalized = spec.trim();
    if (!normalized) {
        throw new Error('Time control spec is empty');
    }

    const result: ParsedTimeControlSpec = {
        mode: 'time',
        initial: 0,
        byoyomi: 0,
        increment: 0,
        fixedMs: 0,
        depth: null,
        nodes: null,
        marginMs: null,
        allowTimeout: false,
        maxWaitMs: null,
    };

    const parseNonNegativeInt = (value: string, label: string): number => {
        const parsed = Number.parseInt(value, 10);
        if (!Number.isFinite(parsed) || parsed < 0) {
            throw new Error(`${label} must be a non-negative integer`);
        }
        return parsed;
    };

    const [base, ...suffixParts] = normalized.split(';');

    if (base.startsWith('fx')) {
        result.mode = 'fixed';
        const fixedValue = base.slice(2);
        if (!fixedValue) {
            throw new Error('Fixed time control requires milliseconds value');
        }
        result.fixedMs = parseNonNegativeInt(fixedValue, 'fixedMs');
    } else if (base === 's') {
        result.mode = 'search';
    } else if (base.startsWith('t')) {
        result.mode = 'time';
        let body = base.slice(1);
        let tail = '';
        if (body.includes('+')) {
            const [head, tailPart] = body.split('+');
            body = head;
            tail = tailPart ?? '';
        }
        result.initial = body ? parseNonNegativeInt(body, 'initial') : 0;
        if (tail) {
            if (tail.startsWith('b')) {
                result.byoyomi = parseNonNegativeInt(tail.slice(1), 'byoyomi');
            } else if (tail.startsWith('i')) {
                result.increment = parseNonNegativeInt(tail.slice(1), 'increment');
            } else {
                throw new Error(`Unknown time control suffix: ${tail}`);
            }
        }
    } else {
        const [head, tail] = base.split('+');
        result.initial = head ? parseNonNegativeInt(head, 'initial') : 0;
        if (tail) {
            if (tail.startsWith('b')) {
                result.byoyomi = parseNonNegativeInt(tail.slice(1), 'byoyomi');
            } else if (tail.startsWith('i')) {
                result.increment = parseNonNegativeInt(tail.slice(1), 'increment');
            } else {
                throw new Error(`Unknown time control suffix: ${tail}`);
            }
        }
    }

    for (const part of suffixParts) {
        if (!part) continue;
        if (part.startsWith('d')) {
            result.depth = parseNonNegativeInt(part.slice(1), 'depth');
        } else if (part.startsWith('n')) {
            result.nodes = parseNonNegativeInt(part.slice(1), 'nodes');
        } else if (part.startsWith('m')) {
            result.marginMs = parseNonNegativeInt(part.slice(1), 'marginMs');
        } else if (part === 'at') {
            result.allowTimeout = true;
        } else if (part.startsWith('wt')) {
            result.maxWaitMs = parseNonNegativeInt(part.slice(2), 'maxWaitMs');
        } else {
            throw new Error(`Unknown time control modifier: ${part}`);
        }
    }

    return result;
}

export const formatDuration = (msRaw: unknown): string => formatDurationMs(msRaw, { zeroLabel: '0s' });

export function formatNodesCountShort(n: unknown): string {
    const value = Number(n ?? 0);
    if (!Number.isFinite(value) || value <= 0) return '0';
    if (value >= 1e9) return `${(value / 1e9).toFixed(value % 1e9 ? 1 : 0)}G`;
    if (value >= 1e6) return `${(value / 1e6).toFixed(value % 1e6 ? 1 : 0)}M`;
    if (value >= 1e3) return `${(value / 1e3).toFixed(value % 1e3 ? 1 : 0)}K`;
    return `${value}`;
}

export function formatNodesCountDetail(n: unknown): string {
    const value = Number(n ?? 0);
    if (!Number.isFinite(value) || value <= 0) return '-';
    const short = formatNodesCountShort(value);
    const full = value.toLocaleString();
    return short === full ? short : `${short} (${full})`;
}
