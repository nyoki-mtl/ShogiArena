import type { DashboardNoticeOptions, DashboardNoticeVariant } from '@/types/dashboard';
import type { DashboardGamesUtils, GameOutcomeInfo, GameOutcomeKind } from '@/modules/games/types';
import { requestJson as sharedRequestJson } from '@/modules/shared/services/api';
import { assertDashboardCoreGetApiBase, assertDashboardCoreShowNotice } from '@/modules/shared/utils/dashboardCore';

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

const BLACK_WIN_CODES = new Set([0, 4, 8, 12, 16]);
const WHITE_WIN_CODES = new Set([1, 5, 9, 13, 17]);
const DRAW_CODES = new Set([2, 6, 10]);
const PAUSED_CODES = new Set([11]);
const ERROR_CODES = new Set([3, 7, 15, 18, 19]);

export function apiBase(): string {
    const getApiBase = assertDashboardCoreGetApiBase();
    return getApiBase();
}

function showNotice(message: string, variant: DashboardNoticeVariant = 'info', options?: DashboardNoticeOptions): void {
    const notify = assertDashboardCoreShowNotice();
    notify(message, variant, options ?? {});
}

export function canOpenLiveView(status: unknown): boolean {
    if (!status) return false;
    const normalized = String(status).toLowerCase();
    return normalized !== 'pending' && normalized !== 'cancelled';
}

export function evaluateOutcome(status: unknown, resultCode: unknown): GameOutcomeInfo {
    const statusLower = String(status ?? '')
        .trim()
        .toLowerCase();
    const resultCodeValue = Number.isInteger(resultCode) ? Number(resultCode) : null;

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

    if (resultCodeValue !== null) {
        if (BLACK_WIN_CODES.has(resultCodeValue)) {
            return {
                outcome: 'black-win',
                blackClass: null,
                whiteClass: null,
            } satisfies GameOutcomeInfo;
        }
        if (WHITE_WIN_CODES.has(resultCodeValue)) {
            return {
                outcome: 'white-win',
                blackClass: null,
                whiteClass: null,
            } satisfies GameOutcomeInfo;
        }
        if (DRAW_CODES.has(resultCodeValue)) {
            return {
                outcome: 'draw',
                blackClass: null,
                whiteClass: null,
            } satisfies GameOutcomeInfo;
        }
        if (PAUSED_CODES.has(resultCodeValue)) {
            return {
                outcome: 'paused',
                blackClass: null,
                whiteClass: null,
            } satisfies GameOutcomeInfo;
        }
        if (ERROR_CODES.has(resultCodeValue)) {
            return {
                outcome: 'error',
                blackClass: null,
                whiteClass: null,
            } satisfies GameOutcomeInfo;
        }
    }

    return {
        outcome: 'pending',
        blackClass: null,
        whiteClass: null,
    } satisfies GameOutcomeInfo;
}

export const requestJson = sharedRequestJson;

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
