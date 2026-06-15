import type { NormalizedTournamentGame } from '@/modules/tournament/types';
import { computeSfenBasePlies } from '@/modules/shared/utils/sfen';

export interface OpeningEngineColorBreakdown {
    games: number;
    wins: number;
    losses: number;
    draws: number;
}

export interface OpeningEngineOverallBreakdown {
    wins: number;
    losses: number;
    draws: number;
}

export interface OpeningEngineSummary {
    name: string;
    total: number;
    score: number | null;
    blackScore: number | null;
    whiteScore: number | null;
    black: OpeningEngineColorBreakdown;
    white: OpeningEngineColorBreakdown;
    overall: OpeningEngineOverallBreakdown;
}

export interface OpeningPlyStats {
    samples: number;
    averagePlies: number | null;
    minPlies: number | null;
    maxPlies: number | null;
    stdevPlies: number;
    histogram: { bucket: number; bucketValue: number; count: number }[];
}

export interface OpeningRow {
    sfen: string;
    label: string;
    total: number;
    black: number;
    white: number;
    draw: number;
    blackRatio: number;
    whiteRatio: number;
    avgPlies: number | null;
    basePlies: number;
    plyStats: OpeningPlyStats | null;
    engines: OpeningEngineSummary[];
    rowElement?: HTMLTableRowElement | null;
}

interface OpeningEngineAggregate {
    name: string;
    total: number;
    overall: OpeningEngineOverallBreakdown;
    black: OpeningEngineColorBreakdown;
    white: OpeningEngineColorBreakdown;
}

interface OpeningStatsEntry {
    total: number;
    pending: number;
    black: number;
    white: number;
    draw: number;
    plyTotal: number;
    plySamples: number;
    plySqTotal: number;
    plyMin: number | null;
    plyMax: number | null;
    engines: Map<string, OpeningEngineAggregate>;
    basePlies: number;
    plyHistogram: Map<string, number>;
}

function extractPlyCount(game: NormalizedTournamentGame): number | null {
    if (typeof game.totalPlies === 'number' && Number.isFinite(game.totalPlies) && game.totalPlies >= 0) {
        return game.totalPlies;
    }
    return null;
}

function normalizePlyCount(count: number | null): number | null {
    if (!Number.isFinite(count) || (count ?? 0) <= 0) return null;
    return count;
}

function ensureEngineStats(
    entry: OpeningStatsEntry,
    engineName: string | null | undefined,
): OpeningEngineAggregate | null {
    if (!engineName) return null;
    const name = engineName.trim();
    if (!name) return null;
    if (!entry.engines.has(name)) {
        entry.engines.set(name, {
            name,
            total: 0,
            overall: { wins: 0, losses: 0, draws: 0 },
            black: { games: 0, wins: 0, losses: 0, draws: 0 },
            white: { games: 0, wins: 0, losses: 0, draws: 0 },
        });
    }
    return entry.engines.get(name) ?? null;
}

function applyOutcome(
    engineStats: OpeningEngineAggregate | null,
    color: 'black' | 'white',
    outcome: number | null,
): void {
    if (!engineStats) return;
    engineStats.total += 1;
    if (color === 'black') engineStats.black.games += 1;
    else engineStats.white.games += 1;

    if (outcome === 2) {
        engineStats.overall.draws += 1;
        if (color === 'black') engineStats.black.draws += 1;
        else engineStats.white.draws += 1;
        return;
    }

    if (color === 'black') {
        if (outcome === 0) {
            engineStats.overall.wins += 1;
            engineStats.black.wins += 1;
        } else if (outcome === 1) {
            engineStats.overall.losses += 1;
            engineStats.black.losses += 1;
        }
    } else {
        if (outcome === 1) {
            engineStats.overall.wins += 1;
            engineStats.white.wins += 1;
        } else if (outcome === 0) {
            engineStats.overall.losses += 1;
            engineStats.white.losses += 1;
        }
    }
}

export function computeOpeningRows(games: readonly NormalizedTournamentGame[]): OpeningRow[] {
    const stats = new Map<string, OpeningStatsEntry>();

    for (const game of games) {
        const key = game.initialSfen?.length ? game.initialSfen : 'startpos';
        let entry = stats.get(key);
        if (!entry) {
            entry = {
                total: 0,
                pending: 0,
                black: 0,
                white: 0,
                draw: 0,
                plyTotal: 0,
                plySamples: 0,
                plySqTotal: 0,
                plyMin: null,
                plyMax: null,
                engines: new Map(),
                basePlies: computeSfenBasePlies(key),
                plyHistogram: new Map(),
            } satisfies OpeningStatsEntry;
            stats.set(key, entry);
        }

        const outcome = game.resultCategory;
        const blackName = (game.players.black.name ?? '').trim();
        const whiteName = (game.players.white.name ?? '').trim();
        const blackStats = ensureEngineStats(entry, blackName);
        const whiteStats = ensureEngineStats(entry, whiteName);

        const isCompleted = outcome === 0 || outcome === 1 || outcome === 2;
        if (!isCompleted) {
            entry.pending += 1;
            continue;
        }

        entry.total += 1;
        if (outcome === 0) entry.black += 1;
        else if (outcome === 1) entry.white += 1;
        else if (outcome === 2) entry.draw += 1;

        if (blackName) applyOutcome(blackStats, 'black', outcome);
        if (whiteName) applyOutcome(whiteStats, 'white', outcome);

        const plyCount = normalizePlyCount(extractPlyCount(game));
        if (typeof plyCount === 'number' && Number.isFinite(plyCount) && plyCount > 0) {
            entry.plyTotal += plyCount;
            entry.plySamples += 1;
            entry.plySqTotal += plyCount * plyCount;
            if (entry.plyMin === null || plyCount < entry.plyMin) {
                entry.plyMin = plyCount;
            }
            if (entry.plyMax === null || plyCount > entry.plyMax) {
                entry.plyMax = plyCount;
            }

            const totalPlies = (entry.basePlies || 0) + plyCount;
            const bucketKey = totalPlies.toFixed(0);
            entry.plyHistogram.set(bucketKey, (entry.plyHistogram.get(bucketKey) || 0) + 1);
        }
    }

    const rows: OpeningRow[] = Array.from(stats.entries()).map(([sfen, entry]) => {
        const total = entry.total;
        const blackRatio = total ? (entry.black + entry.draw * 0.5) / total : Number.NaN;
        const whiteRatio = total ? (entry.white + entry.draw * 0.5) / total : Number.NaN;
        const avgPlies = entry.plySamples ? entry.plyTotal / entry.plySamples : null;
        const basePlies = entry.basePlies || 0;
        const label = sfen === 'startpos' ? 'startpos' : sfen;
        const engines: OpeningEngineSummary[] = Array.from(entry.engines.values()).map((engine) => {
            const score = engine.total ? (engine.overall.wins + engine.overall.draws * 0.5) / engine.total : null;
            const blackScore = engine.black.games
                ? (engine.black.wins + engine.black.draws * 0.5) / engine.black.games
                : null;
            const whiteScore = engine.white.games
                ? (engine.white.wins + engine.white.draws * 0.5) / engine.white.games
                : null;
            return {
                name: engine.name,
                total: engine.total,
                score,
                blackScore,
                whiteScore,
                black: { ...engine.black },
                white: { ...engine.white },
                overall: { ...engine.overall },
            } satisfies OpeningEngineSummary;
        });
        engines.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));

        const histogram = entry.plyHistogram;
        const plyStats: OpeningPlyStats | null = entry.plySamples
            ? {
                  samples: entry.plySamples,
                  averagePlies: avgPlies,
                  minPlies: entry.plyMin,
                  maxPlies: entry.plyMax,
                  stdevPlies:
                      entry.plySamples > 1
                          ? Math.sqrt(
                                Math.max(0, entry.plySqTotal / entry.plySamples - (avgPlies ?? 0) * (avgPlies ?? 0)),
                            )
                          : 0,
                  histogram: Array.from(histogram.entries()).map(([bucket, count]) => ({
                      bucket: Number(bucket),
                      bucketValue: Number(bucket),
                      count: Number(count),
                  })),
              }
            : null;

        return {
            sfen,
            label,
            total,
            black: entry.black,
            white: entry.white,
            draw: entry.draw,
            blackRatio,
            whiteRatio,
            avgPlies,
            basePlies,
            plyStats,
            engines,
            rowElement: null,
        } satisfies OpeningRow;
    });

    rows.sort((a, b) => b.total - a.total || a.sfen.localeCompare(b.sfen));
    return rows;
}
