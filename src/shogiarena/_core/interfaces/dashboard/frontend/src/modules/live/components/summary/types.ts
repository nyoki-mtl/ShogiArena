import type { RatingDeltaInfo } from '@/modules/tournament/types';
import type { BaseStandingsRow } from '@/modules/shared/types/standings';

// Public-facing view models that the summary UI renders. Treat these as the
// contract between the rendering layer and any upstream normalizers.
export type ExpandedState = {
    name: string;
    mode: 'options' | 'penta';
    opponent?: string | null;
};

export interface ProgressViewModel {
    completed: number;
    total: number;
    indicator: string | undefined;
    cancelled?: number | null;
    isFinal: boolean;
    sprt?: {
        llr?: number;
        lower?: number;
        upper?: number;
        decision?: string;
        games?: number;
    } | null;
    hasPentaData?: boolean;
}

// Keep overlap with Tournament Standings row shape minimal but explicit. We
// reuse base fields (name/rating/games) while allowing live-specific columns
// such as tcShort and WDL string.
export interface StandingsRow extends BaseStandingsRow {
    wdl: string;
    tcShort: string;
    moved: boolean;
    isAnchor: boolean;
    delta?: { text: string; tone: 'pos' | 'neg' | 'neu' };
    ci?: number;
}

export type PairwiseCell = Record<string, number> & { d?: number };

export interface StandingsViewOptions {
    doc: Document;
    rows: StandingsRow[];
    ratingDeltas: Map<string, RatingDeltaInfo>;
    usingBTD: boolean;
    ciMap: Map<string, number>;
    previousOrder: string[];
    expanded: ExpandedState | null;
    renderOptionsPane: (name: string) => HTMLElement;
    renderPentaPane: (name: string, initialOpponent?: string | null) => HTMLElement;
    onExpandedChange: (state: ExpandedState | null) => void;
    onHighlightChange: (name: string | null) => void;
}

export function isExpandedState(value: unknown): value is ExpandedState {
    if (!value || typeof value !== 'object') return false;
    const candidate = value as { name?: unknown; mode?: unknown };
    return typeof candidate.name === 'string' && (candidate.mode === 'options' || candidate.mode === 'penta');
}
