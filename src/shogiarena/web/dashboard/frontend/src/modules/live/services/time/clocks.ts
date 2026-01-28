import type { LiveClockState } from '@/modules/live/types';
import type { DashboardCore } from '@/types/dashboard';
import { INCREMENT_PLACEHOLDER } from '@/modules/live/utils';
import { formatByoyomi, formatCountUp, formatInc, formatRemain } from './format';
import { parseTimeControlSpec } from './parse';
import { normalizeSFEN } from './sfen';
import type { ClockSide, EngineClockSnapshot } from '@/modules/live/types/time';
import type { MutableJsonObject } from '@/types/shared';

interface DashboardCardState {
    id?: string | number;
    source?: string;
    viewPly?: number;
    autoSync?: boolean;
}

export function computeClocksAtPly(data: EngineClockSnapshot, ply: number): LiveClockState {
    const startIsBlack = (() => {
        const sfen = normalizeSFEN(data.initial_sfen);
        const parts = sfen.split(' ');
        return parts.length > 1 ? parts[1] === 'b' : true;
    })();

    const tcB = parseTimeControlSpec(data.time_control_black || '');
    const tcW = parseTimeControlSpec(data.time_control_white || '');

    if (tcB.mode === 'fixed' || tcW.mode === 'fixed') {
        const fxB = tcB.fixedMs || 0;
        const fxW = tcW.fixedMs || 0;
        return {
            blackRemainMs: fxB,
            whiteRemainMs: fxW,
            incSide: null,
            incMs: 0,
            byoyomiBlack: 0,
            byoyomiWhite: 0,
        };
    }

    let br = (tcB.initial || 0) * 1000;
    let wr = (tcW.initial || 0) * 1000;
    const incB = (tcB.increment || 0) * 1000;
    const incW = (tcW.increment || 0) * 1000;
    const byoB = (tcB.byoyomi || 0) * 1000;
    const byoW = (tcW.byoyomi || 0) * 1000;

    let lastIncSide: ClockSide | null = null;
    let lastIncMs = 0;
    const mt = Array.isArray(data.move_times_ms) ? data.move_times_ms : [];

    for (let k = 1; k <= ply; k += 1) {
        const sideIsBlack = startIsBlack ? k % 2 === 1 : k % 2 === 0;
        const elapsed = Math.max(0, Number(mt[k - 1] ?? 0));
        if (sideIsBlack) {
            if (byoB > 0) {
                br = Math.max(0, br - elapsed);
                lastIncSide = null;
                lastIncMs = 0;
            } else {
                br = Math.max(0, br - elapsed) + incB;
                if (incB > 0) {
                    lastIncSide = 'black';
                    lastIncMs = incB;
                }
            }
        } else {
            if (byoW > 0) {
                wr = Math.max(0, wr - elapsed);
                lastIncSide = null;
                lastIncMs = 0;
            } else {
                wr = Math.max(0, wr - elapsed) + incW;
                if (incW > 0) {
                    lastIncSide = 'white';
                    lastIncMs = incW;
                }
            }
        }
    }

    return {
        blackRemainMs: br,
        whiteRemainMs: wr,
        incSide: lastIncSide,
        incMs: lastIncMs,
        byoyomiBlack: byoB,
        byoyomiWhite: byoW,
    };
}

function isDashboardCardState(value: unknown): value is DashboardCardState {
    if (!value || typeof value !== 'object') return false;
    const candidate = value as { id?: unknown };
    const id = candidate.id;
    return typeof id === 'string' || typeof id === 'number';
}

export interface StaticClockDeps {
    state: DashboardCore['state'];
}

export function createStaticClockUpdater({ state }: StaticClockDeps) {
    return function updateStaticClocksForCard(cardId: string | number, data: EngineClockSnapshot): void {
        const cardList = Array.isArray(state.cards) ? (state.cards as unknown[]) : [];
        const cardState = cardList.find(
            (entry): entry is DashboardCardState => isDashboardCardState(entry) && entry.id === cardId,
        );
        if (!cardState || !data) return;

        let effective: EngineClockSnapshot = data;
        const hasTC = Boolean(data.time_control_black || data.time_control_white);
        if (!hasTC && typeof cardState.source === 'string' && cardState.source.startsWith('worker-latest:')) {
            const workerIdx = Number(cardState.source.split(':')[1]);
            if (!Number.isNaN(workerIdx)) {
                const ws = (state.workers.get(workerIdx) ?? {}) as MutableJsonObject;
                effective = { ...data };
                if (ws.timeControlBlack) effective.time_control_black = String(ws.timeControlBlack);
                if (ws.timeControlWhite) effective.time_control_white = String(ws.timeControlWhite);
            }
        }

        const maxPly = Array.isArray(effective.moves) ? effective.moves.length : 0;
        const viewPlyRaw = cardState.viewPly ?? 0;
        const viewPly = Math.min(Number(viewPlyRaw) || 0, maxPly);
        const clocks = computeClocksAtPly(effective, viewPly);
        const remElB = document.getElementById(`black-remaining-${cardId}`);
        const remElW = document.getElementById(`white-remaining-${cardId}`);
        const incElB = document.getElementById(`black-inc-${cardId}`);
        const incElW = document.getElementById(`white-inc-${cardId}`);

        const tcB = parseTimeControlSpec(effective.time_control_black || '');
        const tcW = parseTimeControlSpec(effective.time_control_white || '');
        const hasTimePoolB = tcB.mode === 'fixed' || tcB.initial > 0 || tcB.increment > 0 || tcB.byoyomi > 0;
        const hasTimePoolW = tcW.mode === 'fixed' || tcW.initial > 0 || tcW.increment > 0 || tcW.byoyomi > 0;
        const hasSearchLimit =
            (tcB.depth ?? 0) > 0 || (tcB.nodes ?? 0) > 0 || (tcW.depth ?? 0) > 0 || (tcW.nodes ?? 0) > 0;
        const searchOnly = hasSearchLimit && !hasTimePoolB && !hasTimePoolW;

        const startIsBlack = (() => {
            const sfen = normalizeSFEN(effective.initial_sfen);
            const parts = sfen.split(' ');
            return parts.length > 1 ? parts[1] === 'b' : true;
        })();
        const sideToMoveIsBlack = startIsBlack ? viewPly % 2 === 0 : viewPly % 2 === 1;

        if (remElB) {
            if (searchOnly) {
                remElB.textContent = formatCountUp(0);
                remElB.classList.remove('byoyomi');
            } else {
                const showByo = clocks.blackRemainMs <= 0 && clocks.byoyomiBlack > 0 && sideToMoveIsBlack;
                remElB.textContent = showByo ? formatByoyomi(clocks.byoyomiBlack) : formatRemain(clocks.blackRemainMs);
                remElB.classList.toggle('byoyomi', showByo);
            }
        }
        if (remElW) {
            if (searchOnly) {
                remElW.textContent = formatCountUp(0);
                remElW.classList.remove('byoyomi');
            } else {
                const showByo = clocks.whiteRemainMs <= 0 && clocks.byoyomiWhite > 0 && !sideToMoveIsBlack;
                remElW.textContent = showByo ? formatByoyomi(clocks.byoyomiWhite) : formatRemain(clocks.whiteRemainMs);
                remElW.classList.toggle('byoyomi', showByo);
            }
        }

        if (incElB) incElB.textContent = INCREMENT_PLACEHOLDER;
        if (incElW) incElW.textContent = INCREMENT_PLACEHOLDER;
        if (viewPly > 0 && clocks.incSide && clocks.incMs > 0) {
            const incStr = `+${formatInc(clocks.incMs)}`;
            if (clocks.incSide === 'black' && incElB) incElB.textContent = incStr;
            if (clocks.incSide === 'white' && incElW) incElW.textContent = incStr;
        }
    };
}
