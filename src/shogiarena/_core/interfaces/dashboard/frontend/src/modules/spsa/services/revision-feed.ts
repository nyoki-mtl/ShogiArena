import type { JsonObject } from '@/types/shared';
import type { SpsaConnectionStatus } from '@/modules/spsa/types';

const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

type RevisionEventSource = {
    close: () => void;
    onopen: ((event: Event) => void) | null;
    onmessage: ((event: MessageEvent<string>) => void) | null;
    onerror: ((event: Event) => void) | null;
    addEventListener: (type: string, listener: (event: MessageEvent<string>) => void) => void;
};

type RevisionFeedDeps = {
    buildUrl: () => string;
    isOffline: () => boolean;
    createEventSource: (url: string) => RevisionEventSource;
    setConnection: (source: RevisionEventSource | null, status: SpsaConnectionStatus) => void;
    refreshSnapshot: (gapDetected: boolean) => Promise<void>;
    markAnalysisDataStale: () => void;
    clearDisconnectNotice: () => void;
    reportFailure: (context: string, error: unknown, options?: { notifyOffline?: boolean; title?: string }) => void;
    setTimer?: (callback: () => void, delayMs: number) => number;
    clearTimer?: (timerId: number) => void;
};

export type RevisionFeedController = {
    open: () => void;
    hide: () => void;
    goOffline: () => void;
    recover: () => void;
    close: () => void;
    reset: () => void;
};

type RevisionPayload = {
    revision: number;
    gapDetected: boolean;
    terminal: boolean;
};

function parseRevisionPayload(raw: string): RevisionPayload {
    const payload = JSON.parse(raw) as JsonObject;
    const data = payload.data;
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
        throw new Error('SPSA revision payload requires data');
    }
    const revision = Number((data as JsonObject).revision);
    if (!Number.isInteger(revision) || revision < 0) {
        throw new Error('SPSA revision payload requires a non-negative revision');
    }
    return {
        revision,
        gapDetected: (data as JsonObject).gap_detected === true,
        terminal: (data as JsonObject).terminal === true,
    };
}

export function createRevisionFeedController(deps: RevisionFeedDeps): RevisionFeedController {
    const setTimer = deps.setTimer ?? ((callback, delayMs) => window.setTimeout(callback, delayMs));
    const clearTimer = deps.clearTimer ?? ((timerId) => window.clearTimeout(timerId));

    let source: RevisionEventSource | null = null;
    let reconnectTimer: number | null = null;
    let reconnectAttempt = 0;
    let lastRevision: number | null = null;
    let suspended = false;
    let terminal = false;
    let terminalSnapshotPending = false;
    let generation = 0;

    const clearReconnectTimer = (): void => {
        if (reconnectTimer === null) {
            return;
        }
        clearTimer(reconnectTimer);
        reconnectTimer = null;
    };

    const closeSource = (): void => {
        if (!source) {
            return;
        }
        const closing = source;
        source = null;
        try {
            closing.close();
        } catch (error) {
            console.warn('[SPSA] Failed to close revision feed', error);
        }
    };

    const setStatus = (status: SpsaConnectionStatus): void => {
        deps.setConnection(source, status);
    };

    const scheduleReconnect = (): void => {
        if (suspended || terminal || terminalSnapshotPending || deps.isOffline() || reconnectTimer !== null) {
            return;
        }
        const delayMs = Math.min(INITIAL_RECONNECT_DELAY_MS * 2 ** reconnectAttempt, MAX_RECONNECT_DELAY_MS);
        reconnectAttempt += 1;
        reconnectTimer = setTimer(() => {
            reconnectTimer = null;
            open();
        }, delayMs);
    };

    const scheduleTerminalSnapshotRecovery = (gapDetected: boolean): void => {
        if (suspended || terminal || !terminalSnapshotPending || deps.isOffline() || reconnectTimer !== null) {
            return;
        }
        const delayMs = Math.min(INITIAL_RECONNECT_DELAY_MS * 2 ** reconnectAttempt, MAX_RECONNECT_DELAY_MS);
        reconnectAttempt += 1;
        reconnectTimer = setTimer(() => {
            reconnectTimer = null;
            recoverSnapshot(gapDetected, 'SpsaRevisionFeed.terminalRecovery', true);
        }, delayMs);
    };

    const recoverSnapshot = (gapDetected: boolean, context: string, terminalSnapshot = false): void => {
        const expectedSource = source;
        const expectedGeneration = generation;
        if (!terminal) {
            setStatus('recovering');
        }
        void deps
            .refreshSnapshot(gapDetected)
            .then(() => {
                if (expectedGeneration !== generation) {
                    return;
                }
                if (terminalSnapshot && terminalSnapshotPending) {
                    terminalSnapshotPending = false;
                    terminal = true;
                    reconnectAttempt = 0;
                    setStatus('closed');
                    return;
                }
                if (!terminal && source === expectedSource && source !== null) {
                    setStatus('open');
                }
            })
            .catch((error) => {
                if (expectedGeneration !== generation) {
                    return;
                }
                deps.reportFailure(context, error, {
                    notifyOffline: true,
                    title: gapDetected ? 'Failed to recover SPSA revision gap' : 'Failed to refresh SPSA revision',
                });
                if (terminalSnapshot) {
                    scheduleTerminalSnapshotRecovery(gapDetected);
                }
            });
    };

    const handleMessage = (event: MessageEvent<string>): void => {
        try {
            const payload = parseRevisionPayload(event.data);
            const localGap = lastRevision !== null && payload.revision > lastRevision + 1;
            const gapDetected = payload.gapDetected || localGap;
            lastRevision = lastRevision === null ? payload.revision : Math.max(lastRevision, payload.revision);
            deps.markAnalysisDataStale();
            if (payload.terminal) {
                terminalSnapshotPending = true;
                clearReconnectTimer();
                closeSource();
                setStatus('recovering');
                recoverSnapshot(gapDetected, 'SpsaRevisionFeed.terminalRefresh', true);
                return;
            }
            recoverSnapshot(gapDetected, 'SpsaRevisionFeed.refresh');
        } catch (error) {
            deps.reportFailure('SpsaRevisionFeed.onmessage', error);
            recoverSnapshot(true, 'SpsaRevisionFeed.malformedRecovery');
        }
    };

    const open = (): void => {
        if (source || suspended || terminal || terminalSnapshotPending) {
            return;
        }
        if (deps.isOffline()) {
            setStatus('offline');
            return;
        }
        clearReconnectTimer();
        try {
            source = deps.createEventSource(deps.buildUrl());
            setStatus(reconnectAttempt > 0 ? 'recovering' : 'connecting');
            const currentSource = source;
            currentSource.onopen = () => {
                if (source !== currentSource) {
                    return;
                }
                reconnectAttempt = 0;
                setStatus('open');
                deps.clearDisconnectNotice();
            };
            currentSource.addEventListener('spsa_revision', handleMessage);
            currentSource.onmessage = handleMessage;
            currentSource.onerror = (error) => {
                if (source !== currentSource) {
                    return;
                }
                closeSource();
                if (deps.isOffline()) {
                    setStatus('offline');
                    return;
                }
                setStatus('recovering');
                deps.reportFailure('SpsaRevisionFeed.onerror', error, { notifyOffline: true });
                scheduleReconnect();
            };
        } catch (error) {
            closeSource();
            setStatus(deps.isOffline() ? 'offline' : 'recovering');
            deps.reportFailure('SpsaRevisionFeed.open', error, { notifyOffline: true });
            scheduleReconnect();
        }
    };

    const hide = (): void => {
        suspended = true;
        clearReconnectTimer();
        closeSource();
        setStatus('hidden');
    };

    const goOffline = (): void => {
        clearReconnectTimer();
        closeSource();
        setStatus('offline');
    };

    const recover = (): void => {
        if (terminal) {
            setStatus('closed');
            return;
        }
        suspended = false;
        reconnectAttempt = Math.max(1, reconnectAttempt);
        setStatus('recovering');
        if (terminalSnapshotPending) {
            clearReconnectTimer();
            recoverSnapshot(false, 'SpsaRevisionFeed.terminalResume', true);
            return;
        }
        recoverSnapshot(false, 'SpsaRevisionFeed.resume');
        open();
    };

    const close = (): void => {
        suspended = true;
        clearReconnectTimer();
        closeSource();
        setStatus('closed');
    };

    const reset = (): void => {
        generation += 1;
        suspended = false;
        lastRevision = null;
        reconnectAttempt = 0;
        terminal = false;
        terminalSnapshotPending = false;
    };

    return { open, hide, goOffline, recover, close, reset };
}
