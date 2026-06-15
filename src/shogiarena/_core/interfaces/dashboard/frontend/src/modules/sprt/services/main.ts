import { requestJson } from '@/modules/shared/services/api';
import { renderLineChart } from '@/modules/shared/components/line-chart';
import { getLiveViewSnapshotStore } from '@/modules/shared';
import type { DashboardSprtApi, SprtSummaryPayload, SprtTimelinePoint, SprtWindow } from '../types';

const defaultWindow = window as SprtWindow;

const SPRT_REFRESH_INTERVAL_MS = 6000;
const LLR_COLOR = '#eab308';
const BOUND_UPPER_COLOR = '#22c55e';
const BOUND_LOWER_COLOR = '#ef4444';

function formatNumber(value: number | null | undefined, digits: number = 2): string {
    if (value == null || !Number.isFinite(value)) return '-';
    return value.toFixed(digits);
}

function formatPercent(value: number | null | undefined, digits: number = 1): string {
    if (value == null || !Number.isFinite(value)) return '-';
    return `${(value * 100).toFixed(digits)}%`;
}

function formatDecision(value: string | null | undefined): string {
    if (!value) return 'continue';
    return value.replace(/_/g, ' ');
}

function buildSeries(points: SprtTimelinePoint[], selector: (point: SprtTimelinePoint) => number | null | undefined) {
    return points.map((point) => ({ x: point.game_index, y: selector(point) ?? null }));
}

function renderCharts(payload: SprtSummaryPayload): void {
    const timeline = payload.timeline ?? [];
    const container = document.getElementById('sprtTimelineChart');
    if (!container) return;

    renderLineChart(
        container,
        [
            { id: 'llr', label: 'LLR', color: LLR_COLOR, points: buildSeries(timeline, (p) => p.llr ?? null) },
            {
                id: 'upper',
                label: 'Upper',
                color: BOUND_UPPER_COLOR,
                points: buildSeries(timeline, (p) => p.upper ?? null),
            },
            {
                id: 'lower',
                label: 'Lower',
                color: BOUND_LOWER_COLOR,
                points: buildSeries(timeline, (p) => p.lower ?? null),
            },
        ],
        {
            yFormatter: (value) => formatNumber(value, 2),
            showLegend: true,
        },
    );
}

function renderSummary(payload: SprtSummaryPayload, owner: SprtWindow): void {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be initialized before SPRT module');
    }
    if (!payload.live_view) {
        throw new Error('SPRT summary payload must include live_view');
    }
    getLiveViewSnapshotStore(core).hydrateFromPayload(payload.live_view, 'sprt.summary.live_view');

    core.updateElement('sprtTestedName', payload.tested ?? '-');
    core.updateElement('sprtBaselineName', payload.baseline ?? '-');

    const status = payload.status ?? {};
    const wins = status.wins ?? 0;
    const losses = status.losses ?? 0;
    const draws = status.draws ?? 0;
    const completed = status.games ?? wins + losses + draws;

    core.updateElement('sprtScoreValue', `${wins}-${losses}-${draws}`);
    core.updateElement('sprtScoreMeta', `Decision: ${formatDecision(status.decision ?? null)}`);

    core.updateElement('sprtLlrValue', formatNumber(status.llr ?? null, 3));
    core.updateElement('sprtLlrMeta', `Win rate ${formatPercent(status.win_rate ?? null)}`);

    const bounds = `${formatNumber(status.lower ?? null, 2)} .. ${formatNumber(status.upper ?? null, 2)}`;
    core.updateElement('sprtBounds', bounds);
    core.updateElement('sprtBoundsMeta', 'LLR bounds');

    core.updateElement('sprtGames', `${completed}`);
    const total = payload.games?.total ?? payload.config?.max_games ?? null;
    const progress = total ? `${completed} / ${total}` : `${completed}`;
    core.updateElement('sprtProgress', progress);

    const timestamp = payload.timestamp ? new Date(payload.timestamp) : null;
    if (timestamp && Number.isFinite(timestamp.getTime())) {
        core.updateElement('sprtTimelineMeta', timestamp.toLocaleTimeString());
    }

    renderCharts(payload);
}

async function fetchSummary(owner: SprtWindow): Promise<void> {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be initialized before SPRT module');
    }
    const apiBase = core.getApiBase();
    const payload = await requestJson<SprtSummaryPayload>(`${apiBase}/api/sprt/summary`, { cache: 'no-cache' });
    renderSummary(payload, owner);
}

export function installSprtModule(owner: SprtWindow = defaultWindow): DashboardSprtApi {
    if (owner.DashboardSprt) {
        return owner.DashboardSprt;
    }

    const state = {
        active: false,
        timerId: null as number | null,
    };

    const api: DashboardSprtApi = {
        setActive(active: boolean) {
            state.active = !!active;
            if (state.active) {
                api.refresh();
                if (state.timerId == null) {
                    state.timerId = owner.setInterval(() => {
                        api.refresh();
                    }, SPRT_REFRESH_INTERVAL_MS) as unknown as number;
                }
            } else if (state.timerId != null) {
                owner.clearInterval(state.timerId);
                state.timerId = null;
            }
        },
        refresh() {
            fetchSummary(owner).catch((error) => {
                owner.DashboardCore?.showApiError('SPRT summary failed', error);
            });
        },
        getState() {
            return { ...state };
        },
    };

    owner.DashboardSprt = api;
    return api;
}
