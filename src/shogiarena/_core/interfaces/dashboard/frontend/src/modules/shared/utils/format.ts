export interface FormatDurationOptions {
    /**
     * Label to use when the duration is exactly zero.
     * Defaults to '-'.
     */
    readonly zeroLabel?: string;
}

export function formatDurationMs(value: unknown, options: FormatDurationOptions = {}): string {
    const ms = Number(value);
    if (!Number.isFinite(ms) || ms < 0) {
        return '-';
    }
    if (ms === 0) {
        return options.zeroLabel ?? '-';
    }
    if (ms >= 3_600_000 && ms % 3_600_000 === 0) {
        return `${Math.round(ms / 3_600_000)}h`;
    }
    if (ms >= 60_000 && ms % 60_000 === 0) {
        return `${Math.round(ms / 60_000)}m`;
    }
    if (ms >= 1_000) {
        const seconds = ms / 1_000;
        return `${seconds.toFixed(ms % 1_000 === 0 ? 0 : 1)}s`;
    }
    return `${Math.round(ms)}ms`;
}

export function formatPercentage(value: unknown, fractionDigits = 1): string {
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return '-';
    }
    return `${(num * 100).toFixed(fractionDigits)}%`;
}

export function abbreviateName(name: unknown, maxLen = 16): string {
    const value = typeof name === 'string' ? name : String(name ?? '');
    if (maxLen <= 0) {
        throw new Error('abbreviateName requires a positive maxLen');
    }
    if (value.length <= maxLen) return value;
    return `${value.slice(0, Math.max(0, maxLen - 3))}...`;
}

export function hashString(str: string): number {
    let hash = 0;
    if (!str) return hash;
    for (let i = 0; i < str.length; i += 1) {
        const char = str.charCodeAt(i);
        hash = (hash << 5) - hash + char;
        hash |= 0; // eslint-disable-line no-bitwise
    }
    return Math.abs(hash);
}
