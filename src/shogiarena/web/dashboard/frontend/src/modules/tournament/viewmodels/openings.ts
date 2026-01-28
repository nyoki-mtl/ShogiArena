import type { OpeningPlyHistogramBin } from '@/modules/tournament/types';
import type { OpeningStatsRowView } from '@/modules/tournament/components/openings';
import { formatAdjustedPlies, formatWinRatio } from '@/modules/shared/viewmodels/format';

export interface OpeningStatsDisplay {
    readonly rank: number;
    readonly label: string;
    readonly sfen: string;
    readonly plyDisplay: string;
    readonly blackWinRatioDisplay: string;
    readonly whiteWinRatioDisplay: string;
    readonly total: number;
    readonly black: number;
    readonly draw: number;
    readonly white: number;
}

export function buildOpeningStatsDisplay(row: OpeningStatsRowView, index: number): OpeningStatsDisplay {
    return {
        rank: index + 1,
        label: row.label || (row.sfen === 'startpos' ? 'startpos' : row.sfen),
        sfen: row.sfen,
        plyDisplay: formatAdjustedPlies(row.avgPlies, row.basePlies),
        blackWinRatioDisplay: formatWinRatio(row.blackRatio),
        whiteWinRatioDisplay: formatWinRatio(row.whiteRatio),
        total: row.total,
        black: row.black,
        draw: row.draw,
        white: row.white,
    } satisfies OpeningStatsDisplay;
}

export function normalizeHistogram(bins: OpeningPlyHistogramBin[] | null | undefined): OpeningPlyHistogramBin[] {
    if (!Array.isArray(bins)) return [];
    return bins.filter((item) => Number.isFinite(item?.bucketValue) && Number.isFinite(item?.count));
}
