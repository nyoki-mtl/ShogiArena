import type { SpsaDashboardState, NormalizedSpsaParameter, NormalizedSpsaUpdateEntry } from '@/modules/spsa/types';
import { escapeHtml } from '@/modules/shared/utils/html';

const MOVERS_CONTAINER_ID = 'moversContainer';
const TOP_MOVERS = 8;
const BOUNDARY_THRESHOLD_RATIO = 0.05;

export function renderMovers(state: SpsaDashboardState): void {
    const container = document.getElementById(MOVERS_CONTAINER_ID);
    if (!container) return;

    const latest = resolveLatestUpdate(state.updates.entries);
    const gradients = latest?.gradients ?? null;
    removeLoading(container);

    if (!gradients || !Object.keys(gradients).length) {
        container.innerHTML = '<p>No gradient data available</p>';
        return;
    }

    const paramMap = buildParamMap(state.params.data?.params ?? []);
    const movers = computeMovers(gradients, paramMap);
    if (!movers.length) {
        container.innerHTML = '<p>No movers data available</p>';
        return;
    }

    const limited = movers.slice(0, TOP_MOVERS);
    container.innerHTML = limited
        .map((mover) => {
            const grad = mover.gradient >= 0 ? `+${mover.gradient.toFixed(4)}` : mover.gradient.toFixed(4);
            const warningClass = mover.boundaryWarning ? ' boundary-warning' : '';
            const warningIcon = mover.boundaryWarning ? ' ⚠️' : '';
            return `
                <div class="mover-item${warningClass}">
                    <span class="mover-name">${escapeHtml(mover.name)}${warningIcon}</span>
                    <span class="mover-grad">${grad}</span>
                </div>
            `;
        })
        .join('');
}

interface MoversCandidate {
    name: string;
    gradient: number;
    absGradient: number;
    boundaryWarning: boolean;
}

function resolveLatestUpdate(entries: readonly NormalizedSpsaUpdateEntry[]): NormalizedSpsaUpdateEntry | null {
    if (!entries.length) return null;
    return entries[0];
}

function buildParamMap(params: readonly NormalizedSpsaParameter[]): Map<string, NormalizedSpsaParameter> {
    const map = new Map<string, NormalizedSpsaParameter>();
    for (const param of params) {
        map.set(param.name, param);
    }
    return map;
}

function computeMovers(
    gradients: Record<string, number>,
    params: Map<string, NormalizedSpsaParameter>,
): MoversCandidate[] {
    const results: MoversCandidate[] = [];
    for (const [name, gradient] of Object.entries(gradients)) {
        if (!Number.isFinite(gradient)) continue;
        const spec = params.get(name);
        const boundaryWarning = spec ? isNearBoundary(spec) : false;
        results.push({
            name,
            gradient,
            absGradient: Math.abs(gradient),
            boundaryWarning,
        });
    }
    results.sort((a, b) => b.absGradient - a.absGradient);
    return results;
}

function isNearBoundary(spec: NormalizedSpsaParameter): boolean {
    if (!Number.isFinite(spec.max) || !Number.isFinite(spec.min) || !Number.isFinite(spec.value)) {
        return false;
    }
    const max = spec.max as number;
    const min = spec.min as number;
    const value = spec.value as number;
    const range = max - min;
    if (!Number.isFinite(range) || range <= 0) return false;
    const threshold = range * BOUNDARY_THRESHOLD_RATIO;
    return value <= min + threshold || value >= max - threshold;
}

function removeLoading(container: Element): void {
    container.classList.remove('loading');
}
