import type { WorkerSnapshotRecord } from '@/modules/live/components/cards/types';
import { isTestEnvironment } from '@/modules/shared/utils/env';

type TsshogiPositionModule = typeof import('tsshogi');
let _tsshogiPromise: Promise<TsshogiPositionModule> | null = null;
function loadTsshogi(): Promise<TsshogiPositionModule> {
    if (_tsshogiPromise) return _tsshogiPromise;
    _tsshogiPromise = import('tsshogi');
    return _tsshogiPromise;
}

type CheckerState = {
    gid: string | null;
    initialSFEN: string;
    rev: number;
    checkedLen: number;
    position: unknown;
    lastErrorKey: string | null;
    lastObservedKey: string | null;
};

const statesByWorker = new Map<number, CheckerState>();
const observedKeyByWorker = new Map<number, string>();

let _legalDebugEnabled: boolean | null = null;
function isLegalMoveDebugEnabled(): boolean {
    if (_legalDebugEnabled != null) return _legalDebugEnabled;
    if (isTestEnvironment()) {
        _legalDebugEnabled = false;
        return _legalDebugEnabled;
    }
    try {
        const params = new URLSearchParams(window?.location?.search ?? '');
        const raw = params.get('legalDebug');
        if (raw === '0') {
            _legalDebugEnabled = false;
        } else if (raw === '1') {
            _legalDebugEnabled = true;
        } else {
            // Default enabled (unless explicitly disabled).
            _legalDebugEnabled = true;
        }
    } catch {
        _legalDebugEnabled = true;
    }
    return _legalDebugEnabled;
}

function toStringOrNull(value: unknown): string | null {
    if (value == null) return null;
    const trimmed = String(value).trim();
    return trimmed ? trimmed : null;
}

function toFullSFEN(raw: unknown, InitialPositionSFEN: { STANDARD: string }): string {
    const input = toStringOrNull(raw) ?? 'startpos';
    const normalized = input.replace(/\s+/g, ' ').trim();

    if (
        normalized === 'startpos' ||
        normalized === 'position startpos' ||
        normalized.startsWith('position startpos ')
    ) {
        return InitialPositionSFEN.STANDARD;
    }

    if (normalized.startsWith('position ')) {
        const rest = normalized.slice('position '.length).trim();
        if (rest.startsWith('startpos')) {
            return InitialPositionSFEN.STANDARD;
        }
        if (rest.startsWith('sfen ')) {
            const sfen = rest.slice('sfen '.length);
            return sfen.split(' moves ')[0]?.trim() || InitialPositionSFEN.STANDARD;
        }
    }

    const maybeSfen = normalized.split(' moves ')[0]?.trim() || InitialPositionSFEN.STANDARD;
    if (maybeSfen === 'startpos') return InitialPositionSFEN.STANDARD;
    return maybeSfen;
}

function extractGid(snapshot: WorkerSnapshotRecord | null | undefined): string | null {
    if (!snapshot) return null;
    return (
        toStringOrNull((snapshot as unknown as { game_id?: unknown }).game_id) ??
        toStringOrNull((snapshot as unknown as { gameId?: unknown }).gameId) ??
        toStringOrNull((snapshot as unknown as { gid?: unknown }).gid)
    );
}

function extractRev(snapshot: WorkerSnapshotRecord | null | undefined): number {
    const raw = (snapshot as unknown as { __history_rev?: unknown } | null | undefined)?.__history_rev;
    return typeof raw === 'number' && Number.isFinite(raw) ? Math.trunc(raw) : 0;
}

function extractWsSeqs(snapshot: WorkerSnapshotRecord | null | undefined): {
    moves: number | null;
    state: number | null;
    snapshot: number | null;
    lastTopic: string | null;
} {
    const meta = snapshot as
        | ({
              __ws_seq_moves?: unknown;
              __ws_seq_state?: unknown;
              __ws_seq_snapshot?: unknown;
              __ws_last_topic?: unknown;
          } & Record<string, unknown>)
        | null
        | undefined;
    const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? Math.trunc(v) : null);
    return {
        moves: n(meta?.__ws_seq_moves),
        state: n(meta?.__ws_seq_state),
        snapshot: n(meta?.__ws_seq_snapshot),
        lastTopic: toStringOrNull(meta?.__ws_last_topic),
    };
}

function extractStartingPlyNumber(initialSfen: string): number | null {
    const parts = String(initialSfen ?? '')
        .trim()
        .split(/\s+/g);
    if (parts.length < 4) return null;
    const n = Number.parseInt(parts[3] ?? '', 10);
    return Number.isFinite(n) && n > 0 ? n : null;
}

function summarizeDelta(lastDelta: unknown): Record<string, unknown> | null {
    const deltaObj = lastDelta && typeof lastDelta === 'object' ? (lastDelta as Record<string, unknown>) : null;
    if (!deltaObj) return null;
    return {
        type: deltaObj.type ?? null,
        current_ply: deltaObj.current_ply ?? null,
        move: deltaObj.move ?? null,
        ki2_move: deltaObj.ki2_move ?? null,
    };
}

export function debugCheckWorkerSnapshotLegality(
    workerIdx: number,
    snapshot: WorkerSnapshotRecord | null | undefined,
    lastDelta?: unknown,
): void {
    if (!isLegalMoveDebugEnabled()) return;
    if (typeof workerIdx !== 'number' || !Number.isFinite(workerIdx)) return;
    if (!snapshot) return;

    const gid = extractGid(snapshot);
    const rev = extractRev(snapshot);
    const movesRaw = Array.isArray(snapshot.moves) ? snapshot.moves : [];
    const contiguousLen = (() => {
        let n = 0;
        for (let i = 0; i < movesRaw.length; i += 1) {
            const mv = String(movesRaw[i] ?? '').trim();
            if (!mv) break;
            n += 1;
        }
        return n;
    })();
    const current_plyRaw = snapshot.current_ply;
    const current_ply =
        typeof current_plyRaw === 'number' && Number.isFinite(current_plyRaw)
            ? Math.max(0, Math.trunc(current_plyRaw))
            : movesRaw.length;
    const targetLen = Math.max(0, Math.min(movesRaw.length, current_ply));
    const lastMoveRaw = targetLen > 0 ? movesRaw[targetLen - 1] : '';
    const lastMove = String(lastMoveRaw ?? '').trim();

    const observedKey = `${gid ?? 'null'}:${rev}:${current_ply}:${targetLen}:${lastMove}`;
    const prevObservedKey = observedKeyByWorker.get(workerIdx);
    if (prevObservedKey === observedKey) return;
    // Mark immediately to avoid scheduling duplicate async validations while the module is still loading.
    observedKeyByWorker.set(workerIdx, observedKey);

    void (async () => {
        const { InitialPositionSFEN, Position } = await loadTsshogi();

        const initialSFENRaw = toStringOrNull(snapshot.initial_sfen) ?? 'startpos';
        const initialSFEN = toFullSFEN(initialSFENRaw, InitialPositionSFEN);
        const initialStartPly = extractStartingPlyNumber(initialSFEN);
        const wsSeq = extractWsSeqs(snapshot);
        const deltaSummary = summarizeDelta(lastDelta);

        const prev = statesByWorker.get(workerIdx);
        const mustReset =
            !prev ||
            prev.gid !== gid ||
            prev.initialSFEN !== initialSFEN ||
            prev.rev !== rev ||
            targetLen < prev.checkedLen;

        const ensurePosition = (): unknown | null => {
            const pos = Position.newBySFEN(initialSFEN);
            if (!pos) {
                console.error('[LiveLegal] invalid initial SFEN', {
                    workerIdx,
                    gid,
                    initialSFEN,
                    raw: snapshot.initial_sfen,
                });
                return null;
            }
            return pos;
        };

        let state: CheckerState | null = null;
        if (mustReset) {
            const pos = ensurePosition();
            if (!pos) return;
            state = {
                gid,
                initialSFEN,
                rev,
                checkedLen: 0,
                position: pos,
                lastErrorKey: prev?.lastErrorKey ?? null,
                lastObservedKey: observedKey,
            };
        } else {
            state = prev;
        }
        if (!state) return;

        const pos = state.position as InstanceType<TsshogiPositionModule['Position']>;
        for (let i = state.checkedLen; i < targetLen; i += 1) {
            const usi = String(movesRaw[i] ?? '').trim();
            if (!usi) {
                const errorKey = `${gid ?? 'null'}:${rev}:${i}:missing:${pos.sfen}`;
                if (state.lastErrorKey !== errorKey) {
                    state.lastErrorKey = errorKey;
                    const around = {
                        prev: i - 1 >= 0 ? String(movesRaw[i - 1] ?? '').trim() : null,
                        cur: usi,
                        next: i + 1 < movesRaw.length ? String(movesRaw[i + 1] ?? '').trim() : null,
                    };
                    let nextNonEmpty: { idx: number; usi: string } | null = null;
                    for (let j = i + 1; j < movesRaw.length; j += 1) {
                        const cand = String(movesRaw[j] ?? '').trim();
                        if (cand) {
                            nextNonEmpty = { idx: j, usi: cand };
                            break;
                        }
                    }
                    console.error('[LiveLegal] missing move at ply', {
                        workerIdx,
                        gid,
                        rev,
                        ply: i + 1,
                        positionBefore: pos.sfen,
                        initialSFEN,
                        initialSFENRaw,
                        initialStartPly,
                        serverSfen: snapshot.sfen ?? null,
                        current_ply,
                        movesLen: movesRaw.length,
                        contiguousLen,
                        delta: deltaSummary,
                        around,
                        nextNonEmpty,
                        wsSeq,
                    });
                }
                state.lastObservedKey = observedKey;
                statesByWorker.set(workerIdx, state);
                return;
            }
            const move = pos.createMoveByUSI(usi);
            if (!move || !pos.isValidMove(move)) {
                const errorKey = `${gid ?? 'null'}:${rev}:${i}:${usi}:${pos.sfen}`;
                if (state.lastErrorKey !== errorKey) {
                    state.lastErrorKey = errorKey;
                    const around = {
                        prev: i - 1 >= 0 ? String(movesRaw[i - 1] ?? '').trim() : null,
                        cur: usi,
                        next: i + 1 < movesRaw.length ? String(movesRaw[i + 1] ?? '').trim() : null,
                    };
                    console.error('[LiveLegal] illegal move', {
                        workerIdx,
                        gid,
                        rev,
                        ply: i + 1,
                        usi,
                        positionBefore: pos.sfen,
                        initialSFEN,
                        initialSFENRaw,
                        initialStartPly,
                        serverSfen: snapshot.sfen ?? null,
                        current_ply,
                        movesLen: movesRaw.length,
                        contiguousLen,
                        delta: deltaSummary,
                        around,
                        wsSeq,
                    });
                }
                state.lastObservedKey = observedKey;
                statesByWorker.set(workerIdx, state);
                return;
            }
            const ok = pos.doMove(move);
            if (!ok) {
                const errorKey = `${gid ?? 'null'}:${rev}:${i}:${usi}:${pos.sfen}:doMove`;
                if (state.lastErrorKey !== errorKey) {
                    state.lastErrorKey = errorKey;
                    console.error('[LiveLegal] doMove failed', {
                        workerIdx,
                        gid,
                        rev,
                        ply: i + 1,
                        usi,
                        positionBefore: pos.sfen,
                        initialSFEN,
                        initialSFENRaw,
                        initialStartPly,
                        serverSfen: snapshot.sfen ?? null,
                        current_ply,
                        movesLen: movesRaw.length,
                        contiguousLen,
                        delta: deltaSummary,
                        wsSeq,
                    });
                }
                state.lastObservedKey = observedKey;
                statesByWorker.set(workerIdx, state);
                return;
            }
            state.checkedLen = i + 1;
        }

        state.lastObservedKey = observedKey;
        statesByWorker.set(workerIdx, state);
    })();
}
