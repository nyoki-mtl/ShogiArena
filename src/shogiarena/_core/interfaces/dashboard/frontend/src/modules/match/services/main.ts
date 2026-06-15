import { requestJson } from '@/modules/shared/services/api';
import { renderLineChart } from '@/modules/shared/components/line-chart';
import { summaryStore } from '@/store';
import { formatDuration, formatNodesCountShort, parseTimeControlSpec } from '@/modules/shared/utils/time-control';
import { getLiveViewSnapshotStore } from '@/modules/shared';
import type { DashboardMatchApi, MatchSummaryPayload, MatchTimelinePoint, MatchWindow } from '../types';

const defaultWindow = window as MatchWindow;

const MATCH_REFRESH_INTERVAL_MS = 6000;
const WIN_RATE_COLOR = '#38bdf8';
const BLACK_COLOR = '#22c55e';
const WHITE_COLOR = '#f97316';

function formatPercent(value: number | null | undefined, digits: number = 1): string {
    if (value == null || !Number.isFinite(value)) return '-';
    return `${(value * 100).toFixed(digits)}%`;
}

function formatElo(value: number | null | undefined, digits: number = 1): string {
    if (value == null || !Number.isFinite(value)) return '-';
    return `${value.toFixed(digits)}`;
}

function formatCountLine(counts: { wins?: number; losses?: number; draws?: number; games?: number } | null): string {
    if (!counts) return '-';
    const wins = counts.wins ?? 0;
    const losses = counts.losses ?? 0;
    const draws = counts.draws ?? 0;
    const games = counts.games ?? wins + losses + draws;
    return `${wins}-${losses}-${draws} (${games})`;
}

function buildSeriesFromTimeline(
    timeline: MatchTimelinePoint[],
    selector: (point: MatchTimelinePoint) => number | null | undefined,
): { x: number; y: number | null }[] {
    return timeline.map((point) => ({ x: point.game_index, y: selector(point) ?? null }));
}

function formatTimeControlShort(spec?: string | null): string | null {
    if (!spec) return null;
    try {
        const tc = parseTimeControlSpec(spec);
        if (tc.mode === 'fixed' && tc.fixedMs) {
            return `fixed ${formatDuration(tc.fixedMs)}`;
        }
        if (tc.mode === 'search') {
            return 'search';
        }
        const parts: string[] = [];
        if (tc.initial && tc.initial > 0) parts.push(formatDuration(tc.initial));
        if (tc.increment && tc.increment > 0) parts.push(`+${formatDuration(tc.increment)}`);
        if (tc.byoyomi && tc.byoyomi > 0) parts.push(`+${formatDuration(tc.byoyomi)} byo`);
        if (tc.depth != null) parts.push(`d${tc.depth}`);
        if (tc.nodes != null) parts.push(`n${formatNodesCountShort(tc.nodes)}`);
        if (!parts.length) return spec;
        return parts.join(' ');
    } catch {
        return spec;
    }
}

function normalizeOptionKey(meta: Record<string, unknown>, key: string): string | null {
    if (Object.hasOwn(meta, key)) return key;
    const lower = key.toLowerCase();
    const matched = Object.keys(meta).find((candidate) => candidate.toLowerCase() === lower);
    return matched ?? null;
}

function resolveOptionValue(meta: Record<string, unknown> | null | undefined, key: string): unknown {
    if (!meta) return null;
    const resolvedKey = normalizeOptionKey(meta, key);
    if (!resolvedKey) return null;
    return meta[resolvedKey];
}

function resolveEngineOption(meta: ReturnType<typeof summaryStore.getEngineMeta>, key: string): unknown {
    if (!meta) return null;
    const runtimeEntry = meta.runtime_usi_options?.[key];
    if (runtimeEntry && runtimeEntry.current != null) {
        return runtimeEntry.current;
    }
    const runtimeKeys = meta.runtime_usi_options ? Object.keys(meta.runtime_usi_options) : [];
    const runtimeMatch = runtimeKeys.find((candidate) => candidate.toLowerCase() === key.toLowerCase());
    if (runtimeMatch) {
        const entry = meta.runtime_usi_options[runtimeMatch];
        if (entry && entry.current != null) {
            return entry.current;
        }
    }
    const resolved = resolveOptionValue(meta.resolved_options ?? {}, key);
    if (resolved != null) return resolved;
    return resolveOptionValue(meta.merged_options ?? {}, key);
}

function formatOptionNumber(value: unknown, suffix?: string): string | null {
    if (value == null) return null;
    if (typeof value === 'number' && Number.isFinite(value)) {
        return suffix ? `${value}${suffix}` : `${value}`;
    }
    const text = String(value).trim();
    if (!text) return null;
    return suffix && !text.endsWith(suffix) ? `${text}${suffix}` : text;
}

function formatPair(label: string, tested?: string | null, baseline?: string | null): string | null {
    if (!tested && !baseline) return null;
    if (tested && baseline && tested !== baseline) {
        return `${label}: ${tested} / ${baseline}`;
    }
    return `${label}: ${tested ?? baseline ?? '-'}`;
}

function renderSummaryLines(payload: MatchSummaryPayload): void {
    const tested = payload.tested ?? '';
    const baseline = payload.baseline ?? '';
    const testedMeta = tested ? summaryStore.getEngineMeta(tested) : undefined;
    const baselineMeta = baseline ? summaryStore.getEngineMeta(baseline) : undefined;

    const tcTested = formatTimeControlShort(summaryStore.getTimeControl(tested) ?? null);
    const tcBaseline = formatTimeControlShort(summaryStore.getTimeControl(baseline) ?? null);
    const threadsTested = formatOptionNumber(resolveEngineOption(testedMeta, 'Threads'));
    const threadsBaseline = formatOptionNumber(resolveEngineOption(baselineMeta, 'Threads'));
    const hashTested = formatOptionNumber(resolveEngineOption(testedMeta, 'Hash'), 'MB');
    const hashBaseline = formatOptionNumber(resolveEngineOption(baselineMeta, 'Hash'), 'MB');

    const elo = payload.elo_estimate ?? null;
    const eloCi = payload.elo_ci95;
    let eloLine = 'Elo: -';
    if (elo != null && Number.isFinite(elo)) {
        if (eloCi && eloCi.lower != null && eloCi.upper != null) {
            const delta = Math.max(Math.abs(elo - eloCi.lower), Math.abs(eloCi.upper - elo));
            eloLine = `Elo: ${formatElo(elo)} +/- ${formatElo(delta)} (95%)`;
        } else {
            eloLine = `Elo: ${formatElo(elo)}`;
        }
    }

    const wins = payload.games?.wins ?? 0;
    const losses = payload.games?.losses ?? 0;
    const draws = payload.games?.draws ?? 0;
    const completed = payload.games?.completed ?? wins + losses + draws;
    const wldLine = `W/L/D: ${wins}-${losses}-${draws} (N=${completed})`;

    const conditions = [
        formatPair('TC', tcTested, tcBaseline),
        formatPair('Threads', threadsTested, threadsBaseline),
        formatPair('Hash', hashTested, hashBaseline),
    ]
        .filter((item): item is string => !!item)
        .join(' | ');
    const conditionsLine = conditions.length ? conditions : 'Conditions: -';

    const core = window.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be initialized before Match summary rendering');
    }
    core.updateElement('matchSummaryLine1', eloLine);
    core.updateElement('matchSummaryLine2', wldLine);
    core.updateElement('matchSummaryLine3', conditionsLine);
}

function renderCharts(payload: MatchSummaryPayload): void {
    const timeline = payload.timeline ?? [];
    const timelineContainer = document.getElementById('matchTimelineChart');
    const colorContainer = document.getElementById('matchColorChart');

    if (timelineContainer) {
        const winSeries = buildSeriesFromTimeline(timeline, (point) => point.win_rate ?? null);
        renderLineChart(
            timelineContainer,
            [
                {
                    id: 'winrate',
                    label: 'Win Rate',
                    color: WIN_RATE_COLOR,
                    points: winSeries,
                },
            ],
            {
                minY: 0,
                maxY: 1,
                yFormatter: (value) => formatPercent(value, 0),
                showLegend: false,
            },
        );
    }

    if (colorContainer) {
        const blackSeries = buildSeriesFromTimeline(timeline, (point) => point.black?.win_rate ?? null);
        const whiteSeries = buildSeriesFromTimeline(timeline, (point) => point.white?.win_rate ?? null);
        renderLineChart(
            colorContainer,
            [
                { id: 'black', label: 'Black', color: BLACK_COLOR, points: blackSeries },
                { id: 'white', label: 'White', color: WHITE_COLOR, points: whiteSeries },
            ],
            {
                minY: 0,
                maxY: 1,
                yFormatter: (value) => formatPercent(value, 0),
                showLegend: true,
            },
        );
    }
}

function renderSummary(payload: MatchSummaryPayload, owner: MatchWindow): void {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be initialized before Match module');
    }
    if (!payload.live_view) {
        throw new Error('Match summary payload must include live_view');
    }
    getLiveViewSnapshotStore(core).hydrateFromPayload(payload.live_view, 'match.summary.live_view');

    core.updateElement('matchTestedName', payload.tested ?? '-');
    core.updateElement('matchBaselineName', payload.baseline ?? '-');

    const wins = payload.games?.wins ?? 0;
    const losses = payload.games?.losses ?? 0;
    const draws = payload.games?.draws ?? 0;
    const completed = payload.games?.completed ?? wins + losses + draws;

    core.updateElement('matchScoreValue', `${wins}-${losses}-${draws}`);
    core.updateElement('matchScoreMeta', `${completed} games`);

    core.updateElement('matchWinRate', formatPercent(payload.win_rate ?? null));
    const rateCi = payload.win_rate_ci95;
    if (rateCi && (rateCi.lower != null || rateCi.upper != null)) {
        core.updateElement('matchWinRateCi', `${formatPercent(rateCi.lower, 1)} .. ${formatPercent(rateCi.upper, 1)}`);
    } else {
        core.updateElement('matchWinRateCi', '-');
    }

    const eloCi = payload.elo_ci95;
    core.updateElement('matchElo', formatElo(payload.elo_estimate ?? null));
    if (eloCi && (eloCi.lower != null || eloCi.upper != null)) {
        core.updateElement('matchEloCi', `${formatElo(eloCi.lower)} .. ${formatElo(eloCi.upper)}`);
    } else {
        core.updateElement('matchEloCi', '-');
    }

    const total = payload.games?.total ?? null;
    const progress = total ? `${completed} / ${total}` : `${completed}`;
    core.updateElement('matchGames', `${completed}`);
    core.updateElement('matchProgress', progress);

    const colors = payload.colors ?? {};
    core.updateElement('matchSenteScore', formatCountLine(colors.black ?? null));
    core.updateElement('matchGoteScore', formatCountLine(colors.white ?? null));

    const timestamp = payload.timestamp ? new Date(payload.timestamp) : null;
    if (timestamp && Number.isFinite(timestamp.getTime())) {
        core.updateElement('matchTimelineMeta', timestamp.toLocaleTimeString());
        core.updateElement('matchColorMeta', timestamp.toLocaleTimeString());
    }

    renderCharts(payload);
    renderSummaryLines(payload);
}

async function fetchSummary(owner: MatchWindow): Promise<void> {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be initialized before Match module');
    }
    const apiBase = core.getApiBase();
    const payload = await requestJson<MatchSummaryPayload>(`${apiBase}/api/match/summary`, { cache: 'no-cache' });
    renderSummary(payload, owner);
}

export function installMatchModule(owner: MatchWindow = defaultWindow): DashboardMatchApi {
    if (owner.DashboardMatch) {
        return owner.DashboardMatch;
    }

    const state = {
        active: false,
        timerId: null as number | null,
    };

    const api: DashboardMatchApi = {
        setActive(active: boolean) {
            state.active = !!active;
            if (state.active) {
                api.refresh();
                if (state.timerId == null) {
                    state.timerId = owner.setInterval(() => {
                        api.refresh();
                    }, MATCH_REFRESH_INTERVAL_MS) as unknown as number;
                }
            } else if (state.timerId != null) {
                owner.clearInterval(state.timerId);
                state.timerId = null;
            }
        },
        refresh() {
            fetchSummary(owner).catch((error) => {
                owner.DashboardCore?.showApiError('Match summary failed', error);
            });
        },
        getState() {
            return { ...state };
        },
    };

    owner.DashboardMatch = api;
    return api;
}
