type ResumePhase = 'idle' | 'critical' | 'deferred';
type ResumeKind = 'visibility' | 'tab' | 'internal';

export type ResumeToken = {
    readonly id: number;
    readonly kind: ResumeKind;
    readonly startedAt: number;
};

export type ResumeCoordinator = {
    begin: (kind: ResumeKind) => ResumeToken;
    end: (token: ResumeToken, options?: { settleDelayMs?: number }) => void;
    defer: (label: string, task: () => void) => void;
    getPhase: () => ResumePhase;
    isCritical: () => boolean;
};

const COORDINATOR_KEY = Symbol('dashboardResumeCoordinator');
const DEFAULT_SETTLE_DELAY_MS = 800;

type ResumeCoordinatorOwner = Window & {
    [COORDINATOR_KEY]?: ResumeCoordinator;
};

type DeferredTask = {
    label: string;
    task: () => void;
};

function scheduleIdle(task: () => void): void {
    if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
        window.requestIdleCallback(() => task());
        return;
    }
    setTimeout(task, 0);
}

export function getResumeCoordinator(owner: ResumeCoordinatorOwner = window): ResumeCoordinator {
    const existing = owner[COORDINATOR_KEY];
    if (existing) {
        return existing;
    }

    let phase: ResumePhase = 'idle';
    let tokenSeq = 0;
    const activeTokens = new Map<number, ResumeToken>();
    const queue: DeferredTask[] = [];
    let flushScheduled = false;
    let settleTimer: ReturnType<typeof setTimeout> | null = null;

    const getPhase = () => phase;
    const isCritical = () => phase === 'critical';

    const flushQueue = () => {
        flushScheduled = false;
        if (phase === 'critical') {
            return;
        }
        if (!queue.length) {
            if (phase === 'deferred') {
                phase = 'idle';
            }
            return;
        }
        const next = queue.shift();
        if (!next) {
            if (phase === 'deferred') {
                phase = 'idle';
            }
            return;
        }
        try {
            next.task();
        } finally {
            if (queue.length > 0) {
                scheduleIdle(flushQueue);
            } else if (phase === 'deferred') {
                phase = 'idle';
            }
        }
    };

    const scheduleFlush = () => {
        if (flushScheduled) return;
        flushScheduled = true;
        scheduleIdle(flushQueue);
    };

    const begin = (kind: ResumeKind): ResumeToken => {
        tokenSeq += 1;
        const startedAt =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const token = { id: tokenSeq, kind, startedAt };
        activeTokens.set(token.id, token);
        phase = 'critical';
        if (settleTimer) {
            clearTimeout(settleTimer);
            settleTimer = null;
        }
        return token;
    };

    const end = (token: ResumeToken, options?: { settleDelayMs?: number }) => {
        if (!activeTokens.has(token.id)) {
            return;
        }
        activeTokens.delete(token.id);
        if (activeTokens.size > 0) {
            return;
        }
        phase = 'deferred';
        const delay = Math.max(0, options?.settleDelayMs ?? DEFAULT_SETTLE_DELAY_MS);
        if (settleTimer) {
            clearTimeout(settleTimer);
        }
        settleTimer = setTimeout(() => {
            settleTimer = null;
            if (phase === 'deferred') {
                scheduleFlush();
            }
        }, delay);
    };

    const defer = (label: string, task: () => void) => {
        if (phase === 'idle') {
            scheduleIdle(task);
            return;
        }
        queue.push({ label, task });
        if (phase === 'deferred') {
            scheduleFlush();
        }
    };

    const coordinator: ResumeCoordinator = {
        begin,
        end,
        defer,
        getPhase,
        isCritical,
    };

    owner[COORDINATOR_KEY] = coordinator;
    return coordinator;
}
