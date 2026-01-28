const MIN_TICK_COUNT = 2;
const DEFAULT_TICK_COUNT = 6;
const EPSILON_MULTIPLIER = 1e-6;

export interface NumericTickInfo {
    readonly min: number;
    readonly max: number;
    readonly step: number;
    readonly ticks: number[];
}

export function computeNiceTicks(
    rawMin: number,
    rawMax: number,
    maxTickCount: number = DEFAULT_TICK_COUNT,
): NumericTickInfo {
    if (!Number.isFinite(rawMin) || !Number.isFinite(rawMax)) {
        return { min: 0, max: 1, step: 1, ticks: [0, 1] };
    }

    let min = rawMin;
    let max = rawMax;

    if (min > max) {
        [min, max] = [max, min];
    }

    if (min === max) {
        const offset = Math.abs(min) || 1;
        min -= offset;
        max += offset;
    }

    const desiredTickCount = Math.max(
        MIN_TICK_COUNT,
        Number.isFinite(maxTickCount) ? Math.floor(maxTickCount) : DEFAULT_TICK_COUNT,
    );
    const range = niceNumber(max - min, false);
    let step = niceNumber(range / Math.max(1, desiredTickCount - 1), true);
    if (!Number.isFinite(step) || step === 0) {
        step = 1;
    }

    const niceMin = Math.floor(min / step) * step;
    const niceMax = Math.ceil(max / step) * step;
    const safeStep = Math.abs(step) || 1;
    const ticks: number[] = [];
    const upperBound = niceMax + safeStep * 0.5;

    for (let tick = niceMin; tick <= upperBound; tick += safeStep) {
        const normalized = alignToStep(tick, safeStep);
        if (ticks.length && Math.abs(normalized - ticks[ticks.length - 1]) < safeStep * EPSILON_MULTIPLIER) {
            continue;
        }
        ticks.push(normalized);
        if (ticks.length > desiredTickCount * 4) {
            break;
        }
    }

    if (ticks.length === 0) {
        ticks.push(alignToStep(niceMin, safeStep), alignToStep(niceMax, safeStep));
    } else if (ticks.length === 1) {
        const only = ticks[0];
        ticks.push(alignToStep(only + safeStep, safeStep));
    }

    return {
        min: alignToStep(niceMin, safeStep),
        max: alignToStep(niceMax, safeStep),
        step: safeStep,
        ticks,
    };
}

export function computeIndexTicks(count: number, maxTickCount: number = DEFAULT_TICK_COUNT): number[] {
    if (!Number.isFinite(count) || count <= 0) {
        return [];
    }

    const latestVariant = Math.max(1, Math.floor(count));
    const maxIndex = latestVariant - 1;

    if (maxIndex <= 0) {
        return [0];
    }

    const stepVariant = determineVariantTickStep(latestVariant, maxTickCount);
    const startVariant = stepVariant === 1 ? 1 : stepVariant;

    const tickSet = new Set<number>();

    const finalTick = Math.max(stepVariant, Math.floor(maxIndex / stepVariant) * stepVariant);

    for (let variant = startVariant; variant <= finalTick; variant += stepVariant) {
        const position = Math.max(0, variant);
        if (position <= maxIndex) {
            tickSet.add(position);
        }
    }

    if (!tickSet.size) {
        tickSet.add(Math.min(stepVariant, maxIndex));
    }

    tickSet.add(0);
    tickSet.add(maxIndex);

    return Array.from(tickSet)
        .filter((value) => Number.isFinite(value) && value >= 0 && value <= maxIndex)
        .sort((a, b) => a - b);
}

export function formatTickLabel(value: number, step: number): string {
    if (!Number.isFinite(value)) return '';
    const magnitude = Math.abs(step);
    if (!Number.isFinite(magnitude) || magnitude === 0) {
        return value.toString();
    }
    const decimals = magnitude >= 1 ? 0 : Math.min(6, Math.ceil(-Math.log10(magnitude)));
    return stripTrailingZeros(value.toFixed(decimals));
}

function niceNumber(range: number, round: boolean): number {
    if (!Number.isFinite(range) || range <= 0) {
        return 1;
    }
    const exponent = Math.floor(Math.log10(range));
    const fraction = range / 10 ** exponent;
    let niceFraction: number;

    if (round) {
        if (fraction < 1.5) niceFraction = 1;
        else if (fraction < 3) niceFraction = 2;
        else if (fraction < 7) niceFraction = 5;
        else niceFraction = 10;
    } else {
        if (fraction <= 1) niceFraction = 1;
        else if (fraction <= 2) niceFraction = 2;
        else if (fraction <= 5) niceFraction = 5;
        else niceFraction = 10;
    }

    return niceFraction * 10 ** exponent;
}

function alignToStep(value: number, step: number): number {
    if (!Number.isFinite(value)) return value;
    if (!Number.isFinite(step) || step === 0) return value;
    const decimals = Math.max(0, Math.ceil(-Math.log10(step)) + 2);
    const rounded = Number.parseFloat(value.toFixed(Math.min(decimals, 10)));
    const threshold = Math.abs(step) * EPSILON_MULTIPLIER;
    if (Math.abs(rounded) < threshold) {
        return 0;
    }
    return rounded;
}

function stripTrailingZeros(value: string): string {
    if (!value.includes('.')) {
        return value;
    }
    return value.replace(/\.0+$/, '').replace(/0+$/, '').replace(/\.$/, '');
}

function determineVariantTickStep(latestVariant: number, maxTickCount: number): number {
    if (latestVariant <= 1) {
        return 1;
    }
    if (latestVariant <= 9) {
        return 1;
    }
    if (latestVariant <= 49) {
        return 5;
    }

    const desiredTicks = Math.max(3, Math.min(maxTickCount, 8));
    const candidateSteps = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000];

    for (const step of candidateSteps) {
        if (latestVariant / step <= desiredTicks) {
            return step;
        }
    }

    return candidateSteps[candidateSteps.length - 1];
}
