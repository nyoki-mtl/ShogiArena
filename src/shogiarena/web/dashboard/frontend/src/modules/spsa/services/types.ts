import type { DashboardCore } from '@/types/dashboard';
import type { SpsaConvergenceResponse, SpsaCorrelationResponse, SpsaLtcResultsResponse } from '@/modules/spsa/types';

export type UpdatesChangedContext = {
    readonly reason: 'entries' | 'variant-games' | 'ltc-games';
    readonly affectedIndices?: readonly number[];
};

export interface SpsaApiCallbacks {
    readonly onSummaryChanged?: () => void;
    readonly onParamsChanged?: () => void;
    readonly onUpdatesChanged?: (context?: UpdatesChangedContext) => void;
    readonly onConvergenceChanged?: (snapshot?: SpsaConvergenceResponse) => void;
    readonly onLtcResultsChanged?: (snapshot?: SpsaLtcResultsResponse) => void;
    readonly onCorrelationChanged?: (snapshot: SpsaCorrelationResponse) => void;
    readonly onAnalysisStale?: () => void;
    readonly onError?: (message: string) => void;
    readonly onClearError?: () => void;
}

export interface SpsaApiOptions {
    readonly core: DashboardCore;
    readonly document: Document;
    readonly callbacks?: SpsaApiCallbacks;
    readonly autoVisibilityRefresh?: boolean;
}
