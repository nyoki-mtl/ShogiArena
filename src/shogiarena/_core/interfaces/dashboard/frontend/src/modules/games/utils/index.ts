import type { DashboardNoticeOptions, DashboardNoticeVariant } from '@/types/dashboard';
import type { DashboardGamesUtils, GameOutcomeInfo, GameOutcomeKind } from '@/modules/games/types';
import { requestJson as sharedRequestJson } from '@/modules/shared/services/api';
import { assertDashboardCoreGetApiBase, assertDashboardCoreShowNotice } from '@/modules/shared/utils/dashboardCore';
import { gameResultOutcomeKind } from '@/modules/shared/utils/gameResult';

interface GamesUtilsWindow extends Window {
    DashboardGamesUtils?: DashboardGamesUtils;
}

const defaultWindow = window as GamesUtilsWindow;

export const STATUS_PRIORITY = Object.freeze(['running', 'pending', 'completed', 'cancelled'] as const);

export const RESULT_LABELS = Object.freeze({
    pending: 'Pending',
    'black-win': 'Black Win',
    'white-win': 'White Win',
    draw: 'Draw',
    cancelled: 'Cancelled',
    paused: 'Paused',
    error: 'Error',
    running: 'Running',
} satisfies Record<GameOutcomeKind, string>);

function apiBase(): string {
    const getApiBase = assertDashboardCoreGetApiBase();
    return getApiBase();
}

function showNotice(message: string, variant?: string, options?: DashboardNoticeOptions): void {
    const notify = assertDashboardCoreShowNotice();
    const normalizedVariant: DashboardNoticeVariant | undefined =
        variant === 'info' || variant === 'success' || variant === 'warn' || variant === 'error' ? variant : undefined;
    notify(message, normalizedVariant, options ?? {});
}

export function canOpenLiveView(status: unknown): boolean {
    if (!status) return false;
    const normalized = String(status).toLowerCase();
    return normalized !== 'pending' && normalized !== 'cancelled';
}

export function evaluateOutcome(status: unknown, gameResult: unknown): GameOutcomeInfo {
    const statusLower = String(status ?? '')
        .trim()
        .toLowerCase();
    const outcome = gameResultOutcomeKind(gameResult);

    if (statusLower === 'running') {
        return {
            outcome: 'running',
            blackClass: null,
            whiteClass: null,
            showRunner: true,
        } satisfies GameOutcomeInfo;
    }

    if (statusLower === 'cancelled') {
        return {
            outcome: 'cancelled',
            blackClass: null,
            whiteClass: null,
        } satisfies GameOutcomeInfo;
    }

    if (outcome !== null) {
        return {
            outcome,
            blackClass: null,
            whiteClass: null,
        } satisfies GameOutcomeInfo;
    }

    return {
        outcome: 'pending',
        blackClass: null,
        whiteClass: null,
    } satisfies GameOutcomeInfo;
}

const gamesUtils: DashboardGamesUtils = Object.freeze({
    STATUS_PRIORITY,
    RESULT_LABELS,
    apiBase,
    showNotice,
    canOpenLiveView,
    evaluateOutcome,
    requestJson: sharedRequestJson,
});

let installedGamesUtils: DashboardGamesUtils | null = null;

export function installGamesUtils(owner: GamesUtilsWindow = defaultWindow): DashboardGamesUtils {
    if (installedGamesUtils) {
        owner.DashboardGamesUtils = installedGamesUtils;
        return installedGamesUtils;
    }

    installedGamesUtils = gamesUtils;
    owner.DashboardGamesUtils = gamesUtils;
    return gamesUtils;
}
