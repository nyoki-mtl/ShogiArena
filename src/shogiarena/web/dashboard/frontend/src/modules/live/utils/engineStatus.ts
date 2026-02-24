export type EngineStateValue =
    | 'queued'
    | 'waiting_for_usiok'
    | 'not_ready'
    | 'waiting_for_readyok'
    | 'ready'
    | 'waiting_for_bestmove'
    | 'ponder'
    | 'waiting_for_ponder_bestmove'
    | 'waiting_for_checkmate'
    | 'will_quit'
    | 'quit_completed';

export interface EngineIoEntry {
    dir: 'in' | 'out';
    line?: string | null;
    ts: number;
    state?: EngineStateValue;
}

export interface EngineStatusEntry {
    state: EngineStateValue;
    io_tail: EngineIoEntry[];
    updated_at_ms: number;
}

export interface EngineStatusSnapshot {
    black: EngineStatusEntry;
    white: EngineStatusEntry;
}

export interface EngineLogLine {
    role: 'black' | 'white';
    dir: 'in' | 'out';
    line: string;
    ts: number;
    state?: EngineStateValue;
}

const READY_STATES: Set<EngineStateValue> = new Set(['ready', 'waiting_for_bestmove']);
const CLOCK_TICK_STATES: Set<EngineStateValue> = new Set([
    'ready',
    'waiting_for_bestmove',
    'ponder',
    'waiting_for_ponder_bestmove',
    'waiting_for_checkmate',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isEngineStateValue(state: unknown): state is EngineStateValue {
    return (
        typeof state === 'string' &&
        (
            [
                'queued',
                'waiting_for_usiok',
                'not_ready',
                'waiting_for_readyok',
                'ready',
                'waiting_for_bestmove',
                'ponder',
                'waiting_for_ponder_bestmove',
                'waiting_for_checkmate',
                'will_quit',
                'quit_completed',
            ] as const
        ).includes(state as EngineStateValue)
    );
}

function isEngineIoEntry(entry: unknown): entry is EngineIoEntry {
    if (!isRecord(entry)) return false;
    if (entry.dir !== 'in' && entry.dir !== 'out') return false;
    if (typeof entry.ts !== 'number' || !Number.isFinite(entry.ts)) return false;
    if (entry.line != null && typeof entry.line !== 'string') return false;
    if (entry.state != null && !isEngineStateValue(entry.state)) return false;
    return true;
}

function isEngineStatusEntry(entry: unknown): entry is EngineStatusEntry {
    if (!isRecord(entry)) return false;
    if (!isEngineStateValue(entry.state)) return false;
    if (!Array.isArray(entry.io_tail) || !entry.io_tail.every(isEngineIoEntry)) return false;
    if (typeof entry.updated_at_ms !== 'number' || !Number.isFinite(entry.updated_at_ms)) return false;
    return true;
}

export function asEngineStatusSnapshot(value: unknown): EngineStatusSnapshot | undefined {
    if (!isRecord(value)) return undefined;
    if (!isEngineStatusEntry(value.black) || !isEngineStatusEntry(value.white)) return undefined;
    return { black: value.black, white: value.white };
}

export function isEngineStateReady(state?: EngineStateValue | string | null): boolean {
    if (!state) return false;
    return READY_STATES.has(state as EngineStateValue);
}

export function shouldStartEngineClock(status?: EngineStatusSnapshot): boolean {
    if (!status) return false;
    return isEngineStateReady(status.black?.state) && isEngineStateReady(status.white?.state);
}

export function shouldRunEngineClock(status?: EngineStatusSnapshot): boolean {
    if (!status) return false;
    const blackState = status.black?.state;
    const whiteState = status.white?.state;
    if (!blackState || !whiteState) return false;
    return CLOCK_TICK_STATES.has(blackState) && CLOCK_TICK_STATES.has(whiteState);
}

export function shouldShowHandshakeOverlay(status?: EngineStatusSnapshot): boolean {
    return !shouldStartEngineClock(status);
}

export function flattenHandshakeLog(status?: EngineStatusSnapshot, maxEntries = 20): EngineLogLine[] {
    if (!status) return [];
    const entries: EngineLogLine[] = [];
    for (const role of ['black', 'white'] as const) {
        const list = status[role]?.io_tail ?? [];
        for (const entry of list) {
            const lineText = entry.line ?? '';
            entries.push({
                role,
                dir: entry.dir,
                line: lineText,
                ts: entry.ts ?? 0,
                state: entry.state,
            });
        }
    }
    entries.sort((a, b) => a.ts - b.ts);
    if (entries.length > maxEntries) {
        entries.splice(0, entries.length - maxEntries);
    }
    return entries;
}
