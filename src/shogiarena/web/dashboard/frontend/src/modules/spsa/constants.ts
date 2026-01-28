export const DEFAULT_CONVERGENCE_DOMAIN = 8;
export const SPSA_SUMMARY_PROGRESS_EVENT = 'spsa:summary-progress';

export type SpsaDetailViewMode = 'slim' | 'full';

export const SPSA_DETAIL_SUPPORTED_INCLUDES = ['variant_games', 'ltc_games', 'score_history', 'raw_payload'] as const;
export type SpsaDetailInclude = (typeof SPSA_DETAIL_SUPPORTED_INCLUDES)[number];

export const SPSA_DETAIL_REQUIRED_INCLUDES = ['variant_games', 'ltc_games'] as const;
export const SPSA_DETAIL_DEFAULT_VIEW: SpsaDetailViewMode = 'slim';

export const SPSA_DETAIL_SUPPORTED_WINDOWS = ['short', 'long'] as const;
export type SpsaDetailWindowMode = (typeof SPSA_DETAIL_SUPPORTED_WINDOWS)[number];
export type SpsaDetailResolvedWindow = SpsaDetailWindowMode | 'full';
export const SPSA_DETAIL_DEFAULT_WINDOW: SpsaDetailWindowMode = 'short';
