export function formatWinRatio(value: number | null | undefined, digits = 3, placeholder = '-'): string {
    if (value === null || value === undefined || Number.isNaN(value)) return placeholder;
    const num = Number(value);
    if (!Number.isFinite(num)) return placeholder;
    return num.toFixed(digits);
}

export function formatSignedDelta(
    value: number | null | undefined,
    digits = 1,
    zeroLabel = '±0',
    placeholder = '—',
): string {
    if (value === null || value === undefined || Number.isNaN(value)) return placeholder;
    const num = Number(value);
    if (!Number.isFinite(num)) return placeholder;
    if (num === 0) return zeroLabel;
    const absValue = Math.abs(num);
    const fixed = Number.isInteger(absValue) && absValue >= 10 ? absValue.toFixed(0) : absValue.toFixed(digits);
    return num > 0 ? `+${fixed}` : `-${fixed}`;
}

export function formatPercent(value: number | null | undefined, digits = 1, placeholder = '-', clamp = true): string {
    if (value === null || value === undefined || Number.isNaN(value)) return placeholder;
    let num = Number(value);
    if (!Number.isFinite(num)) return placeholder;
    if (clamp) {
        num = Math.max(0, Math.min(1, num));
    }
    return `${(num * 100).toFixed(digits)}%`;
}

export function formatAdjustedPlies(avgPlies: number | null, basePlies: number, placeholder = '-'): string {
    const avg = Number.isFinite(avgPlies) ? Number(avgPlies) : null;
    const base = Number.isFinite(basePlies) ? Number(basePlies) : 0;
    if (avg === null) return placeholder;
    return (base + avg).toFixed(1);
}
