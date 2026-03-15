import { formatPercent, formatSignedDelta, formatWinRatio } from '@/modules/shared/viewmodels/format';

export interface MatchupRowViewModel {
    readonly opponent: string;
    readonly wins: number;
    readonly draws: number;
    readonly losses: number;
    readonly winRatio: string;
    readonly delta: string;
    readonly los: string;
}

export function buildMatchupRowViewModel(raw: {
    opponent: string;
    wins: number;
    draws: number;
    losses: number;
    win_ratio: number | null | undefined;
    delta: number | null | undefined;
    los: number | null | undefined;
}): MatchupRowViewModel {
    return {
        opponent: raw.opponent,
        wins: raw.wins,
        draws: raw.draws,
        losses: raw.losses,
        winRatio: formatWinRatio(raw.win_ratio),
        delta: formatSignedDelta(raw.delta, 1, '±0', '—'),
        los: formatPercent(raw.los, 1, '-'),
    } satisfies MatchupRowViewModel;
}
