import { peekWorkerSnapshotRecord } from '@/modules/live/state/updates';
import type { LiveCardState } from '@/modules/live/types';
import type { LiveUpdatesContext } from '@/modules/live/types/updates';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';
import { getResumeCoordinator, type ResumeToken } from '@/modules/shared/services/resume-coordinator';
import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';
import { parseLiveGamesEnvelope, parseLiveSummaryEnvelope } from '@/contracts/parsers/live-ws';
import {
    buildRequestSnapshotMessage,
    buildSetWorkerFilterMessage,
    buildSubscribeMessage,
    type WsClientMessage,
} from '@/contracts/ws-client-messages';
import { bootstrapWorkers } from './bootstrap';
import type { LiveUpdateHandlers } from './handlers';
import { normalizeSummaryPayload } from './normalizers';
import { createSseContractGuard } from './sse-contract-guard';
import { createMergeWorkerBridge } from './worker-bridge';
import type { LiveEnvelope } from './ws-types';

type DisconnectReason = 'error' | 'closed' | 'unsupported';
type ResumeSession = {
    id: number;
    startedAt: number;
    hiddenWindowMs: number;
    activeWorkers: number[];
    connectMs: number | null;
    firstMessageMs: number | null;
    assignmentMs: number | null;
    activeGameSnapshotMs: number | null;
    activeGameGid: string | null;
    workerVmMs: number | null;
    workerVmGid: string | null;
    resumeToken: ResumeToken | null;
};

const WORKER_SNAPSHOT_BOOTSTRAP_GRACE_MS = 800;
const SUMMARY_TOPIC_PREFIX = 'live.summary.snapshot.';
type EngineLogCardState = Pick<LiveCardState, 'engineLogGameKey' | 'engineLogPreference'>;

function isEngineLogCardState(value: unknown): value is EngineLogCardState {
    return typeof value === 'object' && value !== null;
}

function resolveSummaryTopic(runtimeMode: string | null | undefined): string {
    const normalized = (runtimeMode ?? 'tournament').toLowerCase();
    if (normalized === 'spsa') return `${SUMMARY_TOPIC_PREFIX}spsa`;
    if (normalized === 'sprt') return `${SUMMARY_TOPIC_PREFIX}sprt`;
    if (normalized === 'match') return `${SUMMARY_TOPIC_PREFIX}match`;
    if (normalized === 'generate') return `${SUMMARY_TOPIC_PREFIX}generate`;
    return `${SUMMARY_TOPIC_PREFIX}tournament`;
}

function clearPendingTopic(pending: Set<string>, topic: string): void {
    pending.delete(topic);
    const parsed = parseGameTopic(topic);
    if (parsed?.kind === 'snapshot') {
        pending.delete(`live.game.${parsed.gid}.moves.diff`);
        pending.delete(`live.game.${parsed.gid}.analysis.diff`);
        pending.delete(`live.game.${parsed.gid}.meta.diff`);
        pending.delete(`live.game.${parsed.gid}.clock.diff`);
        pending.delete(`live.game.${parsed.gid}.engine_status.diff`);
        return;
    }
    if (parsed?.kind === 'diff') {
        pending.delete(`live.game.${parsed.gid}.snapshot`);
    }
}

function isMergeWorkerManagedTopic(topic: string): boolean {
    // "Merge worker" manages move/meta/clock/engine_status integrity for game-scoped streams.
    // Other streams (assignment/summary) are handled in ws.ts and should not force fromSeq=0 replay.
    return topic.startsWith('live.game.');
}

function collectActiveWorkers(context: LiveUpdatesContext): Set<number> {
    const activeWorkers = new Set<number>();
    const cards = Array.isArray(context.state?.cards) ? context.state.cards : [];
    for (const card of cards) {
        if (card && typeof card === 'object') {
            const source = (card as { source?: string }).source;
            if (typeof source === 'string' && source.startsWith('worker-latest:')) {
                const workerIdxStr = source.split(':')[1];
                const workerIdx = parseInt(workerIdxStr ?? '', 10);
                if (!Number.isNaN(workerIdx)) {
                    activeWorkers.add(workerIdx);
                }
            }
        }
    }
    return activeWorkers;
}

function hasWorkerSnapshotData(state: LiveUpdatesContext['state'], workerIdx: number): boolean {
    const snapshot = peekWorkerSnapshotRecord(state, workerIdx);
    if (!snapshot) {
        return false;
    }
    if (snapshot.game_id) {
        return true;
    }
    if (Array.isArray(snapshot.moves) && snapshot.moves.length > 0) {
        return true;
    }
    if (typeof snapshot.current_ply === 'number' && Number.isFinite(snapshot.current_ply) && snapshot.current_ply > 0) {
        return true;
    }
    return false;
}

function buildWsUrl(context: LiveUpdatesContext, workers: Set<number>): string {
    const apiBase = context.getApiBase?.() ?? '';
    const origin = context.owner?.location?.origin ?? window.location.origin;
    const baseUrl = new URL(apiBase || origin, origin);

    const trimmedPath = baseUrl.pathname.endsWith('/') ? baseUrl.pathname.slice(0, -1) : baseUrl.pathname;
    baseUrl.pathname = `${trimmedPath}/ws`;

    if (baseUrl.protocol === 'https:') {
        baseUrl.protocol = 'wss:';
    } else if (baseUrl.protocol === 'http:') {
        baseUrl.protocol = 'ws:';
    }

    if (workers.size > 0) {
        const workersList = Array.from(workers)
            .sort((a, b) => a - b)
            .join(',');
        baseUrl.searchParams.set('workers', workersList);
    }
    baseUrl.searchParams.set('protocol', 'ws');
    return baseUrl.toString();
}

function buildWorkerFilterKey(workers: Set<number>): string {
    if (!workers.size) return '';
    return Array.from(workers)
        .filter((value) => Number.isFinite(value))
        .sort((a, b) => a - b)
        .join(',');
}

type EngineLogRole = 'black' | 'white';

function buildEngineLogDiffTopic(gid: string, role: EngineLogRole): string {
    return `live.engine.${gid}.${role}.io.diff`;
}

function buildTopicKey(topics: Set<string>): string {
    if (!topics.size) return '';
    return Array.from(topics).sort().join('|');
}

function collectActiveGids(assignmentByWorker: Map<number, string | null>, activeWorkers: Set<number>): Set<string> {
    const gids = new Set<string>();
    for (const workerIdx of activeWorkers) {
        const gid = assignmentByWorker.get(workerIdx) ?? null;
        if (typeof gid === 'string' && gid.trim()) {
            gids.add(gid.trim());
        }
    }
    return gids;
}

function parseGameTopic(topic: string): { gid: string; kind: 'diff' | 'snapshot' } | null {
    if (!topic.startsWith('live.game.')) return null;
    if (topic.endsWith('.moves.diff')) {
        const gid = topic.slice('live.game.'.length, -'.moves.diff'.length).trim();
        return gid ? { gid, kind: 'diff' } : null;
    }
    if (topic.endsWith('.analysis.diff')) {
        const gid = topic.slice('live.game.'.length, -'.analysis.diff'.length).trim();
        return gid ? { gid, kind: 'diff' } : null;
    }
    if (topic.endsWith('.meta.diff')) {
        const gid = topic.slice('live.game.'.length, -'.meta.diff'.length).trim();
        return gid ? { gid, kind: 'diff' } : null;
    }
    if (topic.endsWith('.clock.diff')) {
        const gid = topic.slice('live.game.'.length, -'.clock.diff'.length).trim();
        return gid ? { gid, kind: 'diff' } : null;
    }
    if (topic.endsWith('.engine_status.diff')) {
        const gid = topic.slice('live.game.'.length, -'.engine_status.diff'.length).trim();
        return gid ? { gid, kind: 'diff' } : null;
    }
    if (topic.endsWith('.snapshot')) {
        const gid = topic.slice('live.game.'.length, -'.snapshot'.length).trim();
        return gid ? { gid, kind: 'snapshot' } : null;
    }
    return null;
}

function unwrapAssignment(payload: unknown): Record<number, string | null> | null {
    if (!payload || typeof payload !== 'object') return null;
    const inner = (payload as { payload?: unknown }).payload ?? payload;
    if (!inner || typeof inner !== 'object') return null;
    const raw = (inner as { assignments?: unknown }).assignments;
    if (!raw || typeof raw !== 'object') return null;
    const out: Record<number, string | null> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
        const idx = Number.parseInt(k, 10);
        if (!Number.isFinite(idx)) continue;
        out[idx] = typeof v === 'string' && v.trim() ? v.trim() : null;
    }
    return out;
}

function extractAssignmentRev(payload: unknown): number | null {
    if (!payload || typeof payload !== 'object') return null;
    const inner = (payload as { payload?: unknown }).payload ?? payload;
    if (!inner || typeof inner !== 'object') return null;
    const revRaw = (inner as { assignment_rev?: unknown }).assignment_rev;
    if (typeof revRaw === 'number' && Number.isFinite(revRaw)) {
        return Math.max(0, Math.trunc(revRaw));
    }
    return null;
}

export function createWsSetup(context: LiveUpdatesContext, handlers: LiveUpdateHandlers) {
    const { events, notifyDashboardServerStopped } = context;
    const { onSummaryUpdate } = handlers;
    const resumeCoordinator = getResumeCoordinator(context.owner);

    let socket: WebSocket | null = null;
    let wsDisconnected = false;
    let closedByUser = false;
    let terminalShutdownExpected = false;
    // Track whether we've ever received an assignment snapshot (i.e., initial bootstrap is complete).
    // Used to skip tab resume logic during initial load.
    let hasReceivedInitialAssignment = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let visibilitySuspendTimer: ReturnType<typeof setTimeout> | null = null;
    let resumeDeferredCatchupTimer: ReturnType<typeof setTimeout> | null = null;
    let hiddenSinceLastVisible = false;
    let hiddenAtMs: number | null = null;
    let resumeForceEndTimer: ReturnType<typeof setTimeout> | null = null;
    let tabResumeForceEndTimer: ReturnType<typeof setTimeout> | null = null;
    let removeVisibilityListener: (() => void) | null = null;
    let analysisEnabled = true;
    let lastConnectStart: number | null = null;
    let resumeSeq = 0;
    let resumeSession: ResumeSession | null = null;
    let tabResumeToken: ResumeToken | null = null;
    let tabResumeAwaitingSnapshot = false;
    let lastSubscriptionKey = '';
    let lastSubscribedGids = new Set<string>();
    let lastIncludeAnalysis = true;
    let lastWorkerFilterKey: string | null = null;
    let desiredWorkerFilterKey = '';
    let removeCardsListener: (() => void) | null = null;
    let removeEngineLogListener: (() => void) | null = null;
    const engineLogTopicCounts = new Map<string, number>();
    const SNAPSHOT_REASON_UNKNOWN = 0;
    const SNAPSHOT_REASON_WORKER = 1;
    const SNAPSHOT_REASON_CATCHUP_DEFERRED = 3;
    const SNAPSHOT_REASON_VISIBILITY_RESUME = 4;
    const SNAPSHOT_REASON_VALIDATION_ERROR = 5;
    const SNAPSHOT_REASON_TAB_RESUME = 6;
    const SNAPSHOT_REASON_CARD_SUBSCRIBE = 7;

    const workerBridge = createMergeWorkerBridge(
        (topic, fromSeq, reasonCode) => {
            const parsed = parseGameTopic(topic);
            if (parsed?.gid) {
                let assigned = false;
                for (const gid of assignmentByWorker.values()) {
                    if (gid === parsed.gid) {
                        assigned = true;
                        break;
                    }
                }
                if (!assigned) {
                    return;
                }
            }
            const code = typeof reasonCode === 'number' ? reasonCode : SNAPSHOT_REASON_WORKER;
            requestSnapshot(topic, fromSeq, code);
        },
        (vm) => {
            maybeMarkResumeWorkerVm(vm);
            events?.emit?.('live:worker-vm', vm);
        },
    );
    const lastSeqByTopic = new Map<string, number>();
    const pendingSnapshotTopics = new Set<string>();
    const assignmentByWorker = new Map<number, string | null>();
    const lastMoveGapRequestAtByTopic = new Map<string, number>();
    const lastSnapshotRequestFromSeqByTopic = new Map<string, number>();
    const lastSnapshotRequestAtByTopic = new Map<string, number>();
    const lastUnassignedSnapshotLogAtByGid = new Map<string, number>();
    const lastSnapshotRequestLogAtByTopic = new Map<string, number>();
    const lastAssignmentLogAtMsByWorker = new Map<number, number>();
    let lastAssignmentRev: number | null = null;
    // After visibility resume, ignore non-snapshot assignment updates until a snapshot arrives.
    // This prevents processing stale replayed updates that would trigger snapshot requests for old gids.
    let awaitingAssignmentSnapshot = false;
    // After internal tab resume, request game snapshots once assignment snapshot arrives.
    let awaitingTabResumeGameSnapshots = false;
    // In Merge Worker mode we may observe unbounded numbers of gid-scoped topics over long runs.
    // Keep per-topic tracking bounded so performance does not degrade over time.
    const lastSeenAtByTopic = new Map<string, number>(); // topic -> last seen timestamp (ms)
    const TOPIC_TRACK_TTL_MS = 30 * 60 * 1000; // keep roughly aligned with server default retention
    const TOPIC_TRACK_MAX = 5000;
    let lastTopicPruneAt = 0;
    let moveGapListenerAttached = false;
    const summaryTopic = resolveSummaryTopic(context.state?.runtimeMode ?? 'tournament');
    const BASE_SUBSCRIPTIONS = new Set<string>([summaryTopic, 'live.games.delta', 'live.assignment.snapshot']);

    const contractGuard = createSseContractGuard({
        emit: events?.emit?.bind(events),
        notifyDashboardServerStopped,
        closeActive: (reason) => close(reason),
    });

    function touchTopic(topic: string, now: number): void {
        // Update insertion order to behave like LRU.
        if (lastSeenAtByTopic.has(topic)) {
            lastSeenAtByTopic.delete(topic);
        }
        lastSeenAtByTopic.set(topic, now);
    }

    function maybePruneTopicTracking(now: number): void {
        if (now - lastTopicPruneAt < 60_000) {
            return;
        }
        lastTopicPruneAt = now;

        for (const [topic, lastSeen] of lastSeenAtByTopic) {
            if (now - lastSeen <= TOPIC_TRACK_TTL_MS) {
                continue;
            }
            lastSeenAtByTopic.delete(topic);
            lastSeqByTopic.delete(topic);
            lastMoveGapRequestAtByTopic.delete(topic);
            lastSnapshotRequestFromSeqByTopic.delete(topic);
            lastSnapshotRequestAtByTopic.delete(topic);
        }

        while (lastSeenAtByTopic.size > TOPIC_TRACK_MAX) {
            const oldest = lastSeenAtByTopic.keys().next().value as string | undefined;
            if (!oldest) {
                break;
            }
            lastSeenAtByTopic.delete(oldest);
            lastSeqByTopic.delete(oldest);
            lastMoveGapRequestAtByTopic.delete(oldest);
            lastSnapshotRequestFromSeqByTopic.delete(oldest);
            lastSnapshotRequestAtByTopic.delete(oldest);
        }

        recordLiveDiagnosticsMetric('live.ws.topic_tracking', {
            triggered: 1,
            tracked: lastSeenAtByTopic.size,
            seq_topics: lastSeqByTopic.size,
            gap_topics: lastMoveGapRequestAtByTopic.size,
            snapshot_topics: lastSnapshotRequestFromSeqByTopic.size,
        });
    }

    function send(message: WsClientMessage): void {
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        try {
            socket.send(JSON.stringify(message));
        } catch {
            // ignore
        }
    }

    function addEngineLogTopic(topic: string): void {
        const current = engineLogTopicCounts.get(topic) ?? 0;
        engineLogTopicCounts.set(topic, current + 1);
    }

    function removeEngineLogTopic(topic: string): void {
        const current = engineLogTopicCounts.get(topic) ?? 0;
        if (current <= 1) {
            engineLogTopicCounts.delete(topic);
        } else {
            engineLogTopicCounts.set(topic, current - 1);
        }
    }

    function rebuildEngineLogTopicsFromCards(): void {
        engineLogTopicCounts.clear();
        const cards = Array.isArray(context.state?.cards) ? context.state.cards : [];
        for (const card of cards) {
            if (!isEngineLogCardState(card)) {
                continue;
            }
            const gid = typeof card.engineLogGameKey === 'string' ? card.engineLogGameKey.trim() : '';
            if (!gid) {
                continue;
            }
            for (const role of ['black', 'white'] as const) {
                if (card.engineLogPreference?.[role] === true) {
                    addEngineLogTopic(buildEngineLogDiffTopic(gid, role));
                }
            }
        }
    }

    function buildSubscriptionTopics(activeWorkers: Set<number>): { topics: Set<string>; gids: Set<string> } {
        const topics = new Set(BASE_SUBSCRIPTIONS);
        const gids = collectActiveGids(assignmentByWorker, activeWorkers);
        for (const topic of engineLogTopicCounts.keys()) {
            topics.add(topic);
        }
        for (const gid of gids) {
            topics.add(`live.game.${gid}.moves.diff`);
            topics.add(`live.game.${gid}.meta.diff`);
            topics.add(`live.game.${gid}.clock.diff`);
            topics.add(`live.game.${gid}.engine_status.diff`);
            if (analysisEnabled) {
                topics.add(`live.game.${gid}.analysis.diff`);
            }
        }
        return { topics, gids };
    }

    function updateWorkerFilter(activeWorkers: Set<number>, reasonCode: number = SNAPSHOT_REASON_UNKNOWN): void {
        desiredWorkerFilterKey = buildWorkerFilterKey(activeWorkers);
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
        }
        if (desiredWorkerFilterKey === lastWorkerFilterKey) {
            return;
        }
        const workersPayload = desiredWorkerFilterKey
            ? desiredWorkerFilterKey.split(',').map((value) => Number(value))
            : [];
        send(buildSetWorkerFilterMessage(workersPayload));
        lastWorkerFilterKey = desiredWorkerFilterKey;
        awaitingAssignmentSnapshot = true;
        assignmentByWorker.clear();
        lastAssignmentRev = null;
        requestSnapshot('live.assignment.snapshot', null, reasonCode);
    }

    function updateSubscriptions(reasonCode: number = SNAPSHOT_REASON_UNKNOWN): void {
        const activeWorkers = collectActiveWorkers(context);
        const { topics, gids } = buildSubscriptionTopics(activeWorkers);
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
        }
        const nextKey = buildTopicKey(topics);
        if (nextKey === lastSubscriptionKey && analysisEnabled === lastIncludeAnalysis) {
            return;
        }
        send(buildSubscribeMessage(Array.from(topics), analysisEnabled));
        const newGids = new Set<string>();
        for (const gid of gids) {
            if (!lastSubscribedGids.has(gid)) {
                newGids.add(gid);
            }
        }
        lastSubscriptionKey = nextKey;
        lastSubscribedGids = new Set(gids);
        lastIncludeAnalysis = analysisEnabled;
        if (newGids.size) {
            for (const gid of newGids) {
                requestSnapshot(`live.game.${gid}.snapshot`, null, reasonCode);
            }
        }
    }

    function startResumeSession(hiddenWindowMs: number): void {
        const now = getNow();
        const activeWorkers = Array.from(collectActiveWorkers(context));
        resumeSeq += 1;
        const resumeToken = resumeCoordinator.begin('visibility');
        if (resumeForceEndTimer != null) {
            clearTimeout(resumeForceEndTimer);
        }
        resumeForceEndTimer = setTimeout(() => {
            resumeForceEndTimer = null;
            if (resumeSession?.resumeToken) {
                resumeCoordinator.end(resumeSession.resumeToken);
            }
        }, 2000);
        resumeSession = {
            id: resumeSeq,
            startedAt: now,
            hiddenWindowMs,
            activeWorkers,
            connectMs: null,
            firstMessageMs: null,
            assignmentMs: null,
            activeGameSnapshotMs: null,
            activeGameGid: null,
            workerVmMs: null,
            workerVmGid: null,
            resumeToken,
        };
        recordLiveDiagnosticsMetric('live.ws.visibility_resume', {
            triggered: 1,
            window_ms: hiddenWindowMs,
            active_workers: activeWorkers.length,
        });
    }

    function maybeMarkResumeConnect(activeWorkerCount: number): void {
        if (!resumeSession) return;
        if (resumeSession.connectMs != null) return;
        const now = getNow();
        const connectStart = lastConnectStart ?? resumeSession.startedAt;
        resumeSession.connectMs = Math.max(0, now - connectStart);
        recordLiveDiagnosticsMetric('live.ws.resume_connect', {
            triggered: 1,
            id: resumeSession.id,
            latency_ms: resumeSession.connectMs,
            window_ms: resumeSession.hiddenWindowMs,
            workers: activeWorkerCount,
        });
    }

    function maybeMarkResumeFirstMessage(): void {
        if (!resumeSession) return;
        if (resumeSession.firstMessageMs != null) return;
        resumeSession.firstMessageMs = Math.max(0, getNow() - resumeSession.startedAt);
        recordLiveDiagnosticsMetric('live.ws.resume_first_message', {
            triggered: 1,
            id: resumeSession.id,
            latency_ms: resumeSession.firstMessageMs,
        });
    }

    function maybeMarkResumeAssignmentSnapshot(assignmentsCount: number): void {
        if (!resumeSession) return;
        if (resumeSession.assignmentMs != null) return;
        resumeSession.assignmentMs = Math.max(0, getNow() - resumeSession.startedAt);
        recordLiveDiagnosticsMetric('live.ws.resume_assignment', {
            triggered: 1,
            id: resumeSession.id,
            latency_ms: resumeSession.assignmentMs,
            active_workers: resumeSession.activeWorkers.length,
            assignments: assignmentsCount,
        });
    }

    function maybeMarkResumeActiveGameSnapshot(gid: string, movesLen: number | null, current_ply: number | null): void {
        if (!resumeSession) return;
        if (resumeSession.activeGameSnapshotMs != null) return;
        const active = resumeSession.activeWorkers;
        if (!active.length) return;
        const isAssignedActive = active.some((idx) => (assignmentByWorker.get(idx) ?? null) === gid);
        if (!isAssignedActive) return;
        resumeSession.activeGameSnapshotMs = Math.max(0, getNow() - resumeSession.startedAt);
        resumeSession.activeGameGid = gid;
        recordLiveDiagnosticsMetric('live.ws.resume_game_snapshot', {
            triggered: 1,
            id: resumeSession.id,
            latency_ms: resumeSession.activeGameSnapshotMs,
            moves_len: typeof movesLen === 'number' ? movesLen : -1,
            current_ply: typeof current_ply === 'number' ? current_ply : -1,
        });
    }

    function maybeFinishTabResumeSnapshot(gid: string): void {
        if (!tabResumeToken || !tabResumeAwaitingSnapshot) return;
        const activeWorkers = collectActiveWorkers(context);
        if (!activeWorkers.size) return;
        const isAssignedActive = Array.from(activeWorkers).some((idx) => (assignmentByWorker.get(idx) ?? null) === gid);
        if (!isAssignedActive) return;
        resumeCoordinator.end(tabResumeToken);
        tabResumeToken = null;
        tabResumeAwaitingSnapshot = false;
        if (tabResumeForceEndTimer != null) {
            clearTimeout(tabResumeForceEndTimer);
            tabResumeForceEndTimer = null;
        }
    }

    function maybeMarkResumeWorkerVm(vm: unknown): void {
        if (!resumeSession) return;
        if (resumeSession.workerVmMs != null) return;
        if (!vm || typeof vm !== 'object') return;
        const workerIdx = (vm as { workerIdx?: unknown }).workerIdx;
        const gid = (vm as { gid?: unknown }).gid;
        if (typeof workerIdx !== 'number' || !Number.isFinite(workerIdx)) return;
        if (gid !== null && typeof gid !== 'string') return;
        if (!resumeSession.activeWorkers.includes(workerIdx)) return;
        const assigned = assignmentByWorker.get(workerIdx) ?? null;
        if (assigned !== (gid as string | null)) return;
        resumeSession.workerVmMs = Math.max(0, getNow() - resumeSession.startedAt);
        resumeSession.workerVmGid = gid as string | null;
        recordLiveDiagnosticsMetric('live.ws.resume_worker_vm', {
            triggered: 1,
            id: resumeSession.id,
            latency_ms: resumeSession.workerVmMs,
        });
        if (resumeSession.resumeToken) {
            resumeCoordinator.end(resumeSession.resumeToken);
        }
        if (resumeForceEndTimer != null) {
            clearTimeout(resumeForceEndTimer);
            resumeForceEndTimer = null;
        }
        // "Worker shows latest" achieved.
        resumeSession = null;
    }

    function handleMoveGapEvent(_event: Event): void {
        // Merge worker handles move gaps internally; no main-thread handling needed.
        // This handler is kept for potential future use cases but is a no-op.
    }

    function scheduleReconnect(): void {
        if (closedByUser) {
            return;
        }
        lastConnectStart = getNow();
        if (reconnectTimer != null) {
            clearTimeout(reconnectTimer);
        }
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (!closedByUser) {
                start();
            }
        }, 1000);
    }

    function handleDisconnected(reason: DisconnectReason): void {
        if (closedByUser || wsDisconnected) {
            return;
        }
        wsDisconnected = true;
        events?.emit?.('live:sse-disconnected', { reason });
    }

    function cleanupResumeSession(): void {
        if (resumeSession?.resumeToken) {
            resumeCoordinator.end(resumeSession.resumeToken);
        }
        resumeSession = null;
        if (resumeForceEndTimer != null) {
            clearTimeout(resumeForceEndTimer);
            resumeForceEndTimer = null;
        }
    }

    function cleanupTabResumeToken(): void {
        if (tabResumeToken) {
            resumeCoordinator.end(tabResumeToken);
            tabResumeToken = null;
        }
        tabResumeAwaitingSnapshot = false;
        if (tabResumeForceEndTimer != null) {
            clearTimeout(tabResumeForceEndTimer);
            tabResumeForceEndTimer = null;
        }
    }

    function close(reason: 'user' | 'system'): void {
        closedByUser = reason === 'user';
        cleanupResumeSession();
        cleanupTabResumeToken();
        lastSeqByTopic.clear();
        pendingSnapshotTopics.clear();
        assignmentByWorker.clear();
        lastMoveGapRequestAtByTopic.clear();
        lastSnapshotRequestFromSeqByTopic.clear();
        lastSnapshotRequestAtByTopic.clear();
        lastUnassignedSnapshotLogAtByGid.clear();
        lastSnapshotRequestLogAtByTopic.clear();
        lastAssignmentLogAtMsByWorker.clear();
        lastAssignmentRev = null;
        awaitingTabResumeGameSnapshots = false;
        hasReceivedInitialAssignment = false;
        lastSeenAtByTopic.clear();
        lastTopicPruneAt = 0;
        analysisEnabled = true;
        lastSubscriptionKey = '';
        lastSubscribedGids = new Set<string>();
        lastIncludeAnalysis = true;
        lastWorkerFilterKey = null;
        desiredWorkerFilterKey = '';
        engineLogTopicCounts.clear();
        if (removeCardsListener) {
            removeCardsListener();
            removeCardsListener = null;
        }
        if (removeEngineLogListener) {
            removeEngineLogListener();
            removeEngineLogListener = null;
        }
        if (closedByUser && removeVisibilityListener) {
            removeVisibilityListener();
            removeVisibilityListener = null;
        }
        if (
            closedByUser &&
            moveGapListenerAttached &&
            context.owner &&
            typeof context.owner.removeEventListener === 'function'
        ) {
            context.owner.removeEventListener('live:move-gap', handleMoveGapEvent as EventListener);
            moveGapListenerAttached = false;
        }
        if (reconnectTimer != null) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (visibilitySuspendTimer != null) {
            clearTimeout(visibilitySuspendTimer);
            visibilitySuspendTimer = null;
        }
        if (resumeDeferredCatchupTimer != null) {
            clearTimeout(resumeDeferredCatchupTimer);
            resumeDeferredCatchupTimer = null;
        }
        hiddenAtMs = null;
        if (socket) {
            try {
                socket.close();
            } catch {
                // best effort
            }
            socket = null;
        }
    }

    function start(): void {
        if (typeof WebSocket !== 'function') {
            wsDisconnected = true;
            events?.emit?.('live:sse-disconnected', { reason: 'unsupported' });
            const error = new Error('Live updates stream requires WebSocket support.');
            reportDashboardRecoverableFailure(error, {
                scope: 'Live.WsSetup.Unsupported',
                userMessage: 'ライブ更新ストリームを利用するには WebSocket に対応したブラウザが必要です。',
            });
            throw error;
        }
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
            return;
        }
        if (reconnectTimer != null) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }

        terminalShutdownExpected = false;
        rebuildEngineLogTopicsFromCards();
        if (!removeVisibilityListener) {
            installVisibilityListener();
        }
        if (!removeCardsListener) {
            installCardsListener();
        }
        if (!removeEngineLogListener) {
            installEngineLogListener();
        }
        if (!moveGapListenerAttached && context.owner && typeof context.owner.addEventListener === 'function') {
            context.owner.addEventListener('live:move-gap', handleMoveGapEvent as EventListener);
            moveGapListenerAttached = true;
        }

        const activeWorkers = collectActiveWorkers(context);
        desiredWorkerFilterKey = buildWorkerFilterKey(activeWorkers);
        const wsUrl = buildWsUrl(context, activeWorkers);

        closedByUser = false;
        wsDisconnected = false;
        lastConnectStart = getNow();
        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            wsDisconnected = false;
            workerBridge.reset('ws_open');
            // On fresh connection, wait for assignment snapshot before processing diffs.
            awaitingAssignmentSnapshot = true;
            if (!closedByUser) {
                events?.emit?.('live:sse-reconnected');
            }
            events?.emit?.('live:merge-worker-enabled');
            applyVisibilityPolicy();
            const currentWorkers = collectActiveWorkers(context);
            updateWorkerFilter(currentWorkers, SNAPSHOT_REASON_UNKNOWN);
            updateSubscriptions(SNAPSHOT_REASON_UNKNOWN);
            recordConnectSuccess(activeWorkers.size);
            maybeMarkResumeConnect(activeWorkers.size);
            window.setTimeout(() => {
                if (!socket || socket.readyState !== WebSocket.OPEN) {
                    return;
                }
                const missingWorkers = Array.from(collectActiveWorkers(context)).filter(
                    (workerIdx) => !hasWorkerSnapshotData(context.state, workerIdx),
                );
                if (!missingWorkers.length) {
                    return;
                }
                bootstrapWorkers(missingWorkers, {
                    getApiBase: context.getApiBase,
                    onWorkerUpdate: handlers.onWorkerUpdate,
                }).catch((error) => {
                    notifyDashboardServerStopped();
                    // Rethrowing inside this fire-and-forget .catch only produces an
                    // unhandledrejection; report it for diagnostics instead.
                    reportDashboardRecoverableFailure(error, {
                        scope: 'Live.WsSetup.WorkerBootstrap',
                        userMessage: 'Failed to bootstrap live worker snapshots.',
                    });
                });
            }, WORKER_SNAPSHOT_BOOTSTRAP_GRACE_MS);
        };

        socket.onmessage = (event: MessageEvent<string>) => {
            const start = getNow();
            try {
                const envelope = JSON.parse(event.data) as LiveEnvelope;
                const topic = envelope.topic ?? '';
                if (topic === 'live.heartbeat') {
                    return;
                }
                maybeMarkResumeFirstMessage();
                recordLiveDiagnosticsMetric('live.ws.message', { triggered: 1 });
                if (topic.startsWith('live.assignment.')) {
                    const now = getNow();
                    touchTopic(topic, now);
                    maybePruneTopicTracking(now);
                    if (typeof envelope.seq === 'number' && Number.isFinite(envelope.seq)) {
                        lastSeqByTopic.set(topic, envelope.seq);
                    }
                    const next = unwrapAssignment(envelope.payload);
                    if (!next) {
                        requestSnapshot(topic, null);
                        return;
                    }
                    const isAssignmentSnapshot = topic.endsWith('.snapshot');
                    // After visibility resume, ignore non-snapshot assignment updates until a snapshot arrives.
                    // This prevents processing stale replayed updates that trigger snapshot requests for old gids.
                    if (awaitingAssignmentSnapshot && !isAssignmentSnapshot) {
                        clearPendingTopic(pendingSnapshotTopics, topic);
                        workerBridge.handleEnvelope(envelope);
                        return;
                    }
                    if (isAssignmentSnapshot) {
                        awaitingAssignmentSnapshot = false;
                        hasReceivedInitialAssignment = true;
                        assignmentByWorker.clear();
                    }
                    const assignmentRev = extractAssignmentRev(envelope.payload);
                    if (assignmentRev != null && lastAssignmentRev != null && assignmentRev < lastAssignmentRev) {
                        clearPendingTopic(pendingSnapshotTopics, topic);
                        return;
                    }
                    if (assignmentRev != null) {
                        lastAssignmentRev = assignmentRev;
                    }
                    maybeMarkResumeAssignmentSnapshot(Object.keys(next).length);
                    for (const [workerIdxRaw, gid] of Object.entries(next)) {
                        const workerIdx = Number(workerIdxRaw);
                        if (!Number.isFinite(workerIdx)) continue;
                        const prev = assignmentByWorker.get(workerIdx) ?? null;
                        if (prev === gid) continue;
                        assignmentByWorker.set(workerIdx, gid);
                    }
                    updateSubscriptions(SNAPSHOT_REASON_WORKER);
                    // After internal tab resume, request game snapshots now that assignments are populated.
                    if (isAssignmentSnapshot && awaitingTabResumeGameSnapshots) {
                        awaitingTabResumeGameSnapshots = false;
                        requestActiveGameSnapshots(SNAPSHOT_REASON_TAB_RESUME, true);
                        resumeCoordinator.defer('live.ws.tab_resume.catchup', () => requestDeferredCatchupSnapshots());
                        recordLiveDiagnosticsMetric('live.ws.tab_resume_game_snapshots', {
                            triggered: 1,
                            gids: Array.from(assignmentByWorker.values()).filter(Boolean).length,
                        });
                    }
                    clearPendingTopic(pendingSnapshotTopics, topic);
                    workerBridge.handleEnvelope(envelope);
                    return;
                }

                const gameTopic = parseGameTopic(topic);
                if (gameTopic) {
                    const now = getNow();
                    touchTopic(topic, now);
                    maybePruneTopicTracking(now);
                    if (typeof envelope.seq === 'number' && Number.isFinite(envelope.seq)) {
                        lastSeqByTopic.set(topic, envelope.seq);
                    }
                    if (gameTopic.kind === 'snapshot') {
                        const snap =
                            envelope.payload && typeof envelope.payload === 'object'
                                ? (envelope.payload as { snapshot?: unknown }).snapshot
                                : undefined;
                        const movesLen =
                            snap && typeof snap === 'object' && Array.isArray((snap as { moves?: unknown }).moves)
                                ? (snap as { moves: unknown[] }).moves.length
                                : null;
                        const current_ply =
                            snap &&
                            typeof snap === 'object' &&
                            typeof (snap as { current_ply?: unknown }).current_ply === 'number' &&
                            Number.isFinite((snap as { current_ply: number }).current_ply)
                                ? (snap as { current_ply: number }).current_ply
                                : null;
                        maybeMarkResumeActiveGameSnapshot(gameTopic.gid, movesLen, current_ply);
                        maybeFinishTabResumeSnapshot(gameTopic.gid);
                    }
                    workerBridge.handleEnvelope(envelope);
                    return;
                }

                if (topic === summaryTopic) {
                    const summaryStart = getNow();
                    const parsed = (() => {
                        try {
                            return parseLiveSummaryEnvelope(envelope);
                        } catch (error) {
                            requestSnapshot(topic, undefined, SNAPSHOT_REASON_VALIDATION_ERROR);
                            throw error;
                        }
                    })();
                    const afterValidate = getNow();
                    const payload = normalizeSummaryPayload(parsed.payload);
                    const afterNormalize = getNow();
                    clearPendingTopic(pendingSnapshotTopics, topic);
                    if (payload.tournament_ended === true) {
                        terminalShutdownExpected = true;
                    }
                    onSummaryUpdate(payload);
                    const afterApply = getNow();
                    recordLiveDiagnosticsMetric('live.ws.summary_snapshot', {
                        triggered: 1,
                        validate_ms: Math.max(0, afterValidate - summaryStart),
                        normalize_ms: Math.max(0, afterNormalize - afterValidate),
                        apply_ms: Math.max(0, afterApply - afterNormalize),
                        total_ms: Math.max(0, afterApply - summaryStart),
                    });
                    return;
                } else if (topic === 'live.games.delta') {
                    if (!validateSeq(topic, envelope.seq)) {
                        requestSnapshot(topic);
                        return;
                    }
                    const parsed = (() => {
                        try {
                            return parseLiveGamesEnvelope(envelope);
                        } catch (error) {
                            requestSnapshot(topic, undefined, SNAPSHOT_REASON_VALIDATION_ERROR);
                            throw error;
                        }
                    })();
                    clearPendingTopic(pendingSnapshotTopics, topic);
                    events?.emit?.('live:games-delta', parsed.payload);
                    return;
                }

                if (topic.startsWith('live.engine.')) {
                    const now = getNow();
                    touchTopic(topic, now);
                    maybePruneTopicTracking(now);
                    if (!validateSeq(topic, envelope.seq)) {
                        requestSnapshot(topic);
                        return;
                    }
                    pendingSnapshotTopics.delete(topic);
                    const payload = envelope.payload;
                    if (payload && typeof payload === 'object') {
                        const data = payload as {
                            gid?: unknown;
                            role?: unknown;
                            entries?: unknown;
                        };
                        const gid = typeof data.gid === 'string' && data.gid.trim() ? data.gid.trim() : null;
                        const role = data.role === 'black' || data.role === 'white' ? data.role : null;
                        const entries = Array.isArray(data.entries) ? data.entries : [];
                        if (gid && role) {
                            events?.emit?.('live:engine-log', {
                                gid,
                                role,
                                entries,
                                isSnapshot: topic.endsWith('.snapshot'),
                            });
                        }
                    }
                    return;
                }

                if (!validateSeq(topic, envelope.seq)) {
                    requestSnapshot(topic);
                    return;
                }
            } catch (error) {
                contractGuard('live.ws', error);
            } finally {
                const elapsed = Math.max(0, getNow() - start);
                recordLiveDiagnosticsMetric('live.ws.handle_ms', { triggered: 1, latency_ms: elapsed });
            }
        };

        socket.onerror = (err: Event | null) => {
            if (terminalShutdownExpected) {
                stop('user');
                return;
            }
            recordConnectFailure();
            handleDisconnected('error');
            notifyDashboardServerStopped();
            close('system');
            scheduleReconnect();
            const cause = err instanceof ErrorEvent && err.error instanceof Error ? err.error : null;
            const reason = err?.type ? ` (${err.type})` : '';
            const error =
                cause ??
                new Error(
                    `Live updates WebSocket connection failed${reason || ''}`,
                    err instanceof Error ? { cause: err } : undefined,
                );
            reportDashboardRecoverableFailure(error, {
                scope: 'Live.WsSetup.ConnectionError',
                userMessage: 'ライブ更新ストリーム (WebSocket) への接続が中断されました。',
            });
            // Already reported and reconnect scheduled; throwing here is swallowed by the event
            // dispatcher and only adds noise, so do not rethrow.
        };

        socket.onclose = () => {
            if (terminalShutdownExpected) {
                stop('user');
                return;
            }
            recordConnectFailure();
            handleDisconnected('closed');
            close('system');
            scheduleReconnect();
        };
    }

    function stop(reason: 'user' | 'system' = 'user'): void {
        close(reason);
        events?.emit?.('live:merge-worker-disabled');
        workerBridge.dispose();
        // Drop the global diagnostics hook so it does not retain the disposed worker bridge closure.
        delete (window as unknown as { __liveDiagnostics?: () => void }).__liveDiagnostics;
    }

    function isActive(): boolean {
        return socket !== null && socket.readyState === WebSocket.OPEN;
    }

    function validateSeq(topic: string, seq?: number): boolean {
        if (typeof seq !== 'number' || Number.isNaN(seq)) {
            lastSeqByTopic.delete(topic);
            return true;
        }
        const prev = lastSeqByTopic.get(topic);
        if (prev === undefined) {
            lastSeqByTopic.set(topic, seq);
            return true;
        }
        if (seq === prev + 1) {
            lastSeqByTopic.set(topic, seq);
            return true;
        }
        // Gap or rewind detected
        const bucket = classifySeqGapTopic(topic);
        recordLiveDiagnosticsMetric('live.ws.seq_gap', { triggered: 1, [`topic_${bucket}`]: 1 });
        return false;
    }

    function classifySeqGapTopic(topic: string): string {
        if (topic.startsWith('live.game.')) return 'game';
        if (topic.startsWith('live.summary.')) return 'summary';
        if (topic.startsWith('live.games.')) return 'games';
        if (topic.startsWith('live.assignment.')) return 'assignment';
        if (topic.startsWith('live.worker.')) return 'worker';
        if (topic.startsWith('live.engine.')) return 'engine';
        if (topic.startsWith('live.time.')) return 'time';
        if (topic.startsWith('live.spsa.')) return 'spsa';
        if (topic.startsWith('live.updates.')) return 'updates';
        return 'other';
    }

    function requestSnapshot(
        topic: string,
        overrideFromSeq?: number | null,
        reasonCode: number = SNAPSHOT_REASON_UNKNOWN,
    ): void {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
        }
        const now = getNow();
        touchTopic(topic, now);
        maybePruneTopicTracking(now);
        // Gap detection and "pending snapshot" bookkeeping live inside the merge worker.
        // The server may reply to `request_snapshot(topic=*.diff)` with a `*.snapshot` envelope; ws.ts forwards that
        // envelope to the worker and doesn't observe it, so local pending bookkeeping would never clear and would
        // permanently suppress subsequent resync requests.
        const mergeWorkerManaged = isMergeWorkerManagedTopic(topic);
        if (!mergeWorkerManaged && pendingSnapshotTopics.has(topic) && overrideFromSeq !== null) {
            return;
        }
        if (overrideFromSeq === null) {
            const lastRequestedAt = lastSnapshotRequestAtByTopic.get(topic) ?? 0;
            if (now - lastRequestedAt < 500) {
                return;
            }
            lastSnapshotRequestAtByTopic.set(topic, now);
        }
        let fromSeq =
            overrideFromSeq === null
                ? undefined
                : typeof overrideFromSeq === 'number'
                  ? overrideFromSeq
                  : lastSeqByTopic.get(topic);
        // Snapshot topics are "state" fetches; do not replay the server ring buffer on them.
        // Even a small replay can amplify work and delay catch-up after resume.
        if (overrideFromSeq === undefined && topic.endsWith('.snapshot')) {
            fromSeq = undefined;
        }
        // Assignment stream only needs the latest mapping; skip replay to avoid stale gids during resume.
        // Force `fromSeq=undefined` when `overrideFromSeq` is null or undefined (i.e., we want "latest").
        if ((overrideFromSeq === undefined || overrideFromSeq === null) && topic.startsWith('live.assignment.')) {
            fromSeq = undefined;
        }
        // In Merge Worker mode, ws.ts does not track per-topic seq baselines for `live.game.*` streams
        // (the worker does). When we trigger a resync from the main thread (e.g. move-gap events),
        // still send `fromSeq=0` so the server can replay retained diffs.
        //
        // NOTE: avoid forcing `fromSeq=0` for `*.snapshot` topics. Snapshot requests are "state" fetches and
        // replaying the server ring buffer can amplify work during tab resume.
        if (
            mergeWorkerManaged &&
            overrideFromSeq === undefined &&
            typeof fromSeq !== 'number' &&
            !topic.endsWith('.snapshot')
        ) {
            fromSeq = 0;
        }
        // Throttle duplicate resync requests in Merge Worker mode. We can't reliably maintain a "pending" set here
        // (the snapshot reply is forwarded to the worker and ws.ts won't observe it), but we can at least avoid
        // spamming the same `fromSeq` repeatedly when a move-gap keeps firing during catch-up.
        if (
            mergeWorkerManaged &&
            topic.endsWith('.moves.diff') &&
            typeof fromSeq === 'number' &&
            overrideFromSeq === undefined
        ) {
            const lastRequested = lastSnapshotRequestFromSeqByTopic.get(topic);
            if (lastRequested === fromSeq) {
                return;
            }
            lastSnapshotRequestFromSeqByTopic.set(topic, fromSeq);
        }
        const message = buildRequestSnapshotMessage(
            topic,
            typeof fromSeq === 'number' ? fromSeq : undefined,
            reasonCode,
        );
        if (!mergeWorkerManaged) {
            pendingSnapshotTopics.add(topic);
        }
        send(message);
        // Force seq baseline reset after a resync request so the next message can become authoritative.
        // In Merge Worker mode, we keep `lastSeqByTopic` as a best-effort baseline for move-gap driven recovery.
        if (!mergeWorkerManaged) {
            lastSeqByTopic.delete(topic);
        }
        recordLiveDiagnosticsMetric('live.ws.request_snapshot', {
            triggered: 1,
            from_seq: typeof fromSeq === 'number' ? fromSeq : -1,
            reason_code: reasonCode,
        });
    }

    function requestDeferredCatchupSnapshots(): void {
        for (const topic of ['live.games.delta', summaryTopic]) {
            // After a tab resume, we primarily care about the latest state. Replaying a large server ring buffer
            // can delay catch-up and increase main-thread work. Force "latest" snapshots here.
            requestSnapshot(topic, null, SNAPSHOT_REASON_CATCHUP_DEFERRED);
        }
        recordLiveDiagnosticsMetric('live.ws.catchup_deferred', { triggered: 1 });
    }

    function requestActiveGameSnapshots(
        reasonCode: number = SNAPSHOT_REASON_UNKNOWN,
        forceLatest: boolean = false,
    ): void {
        const activeWorkers = collectActiveWorkers(context);
        const gids = new Set<string>();
        for (const workerIdx of activeWorkers) {
            const gid = assignmentByWorker.get(workerIdx) ?? null;
            if (gid) {
                gids.add(gid);
            }
        }
        for (const gid of gids) {
            requestSnapshot(`live.game.${gid}.snapshot`, forceLatest ? null : undefined, reasonCode);
        }
        if (gids.size > 0) {
            recordLiveDiagnosticsMetric('live.ws.resume_game_snapshots', { triggered: 1, gids: gids.size });
        }
    }

    function toggleAnalysis(enabled: boolean): void {
        if (analysisEnabled === enabled) return;
        analysisEnabled = enabled;
        updateSubscriptions(SNAPSHOT_REASON_UNKNOWN);
        recordLiveDiagnosticsMetric('live.ws.analysis_toggle', { triggered: 1, enabled: enabled ? 1 : 0 });
    }

    function applyVisibilityPolicy(): void {
        const isVisible = typeof document === 'undefined' || document.visibilityState !== 'hidden';
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        toggleAnalysis(isVisible);
        if (isVisible) {
            const reasonCode = resumeSession ? SNAPSHOT_REASON_VISIBILITY_RESUME : SNAPSHOT_REASON_UNKNOWN;
            // Prioritize "worker catch-up" first (assignment + active game snapshots).
            // Heavier list snapshots (games / summary) are deferred slightly to improve perceived resume.
            if (resumeSession) {
                assignmentByWorker.clear();
                lastSnapshotRequestAtByTopic.delete('live.assignment.snapshot');
            }
            requestSnapshot('live.assignment.snapshot', null, reasonCode);
            requestActiveGameSnapshots(reasonCode, Boolean(resumeSession));
            if (!resumeSession) {
                requestDeferredCatchupSnapshots();
                return;
            }
            if (resumeDeferredCatchupTimer != null) {
                clearTimeout(resumeDeferredCatchupTimer);
            }
            if (resumeCoordinator.isCritical()) {
                resumeCoordinator.defer('live.ws.deferred-catchup', () => requestDeferredCatchupSnapshots());
            } else {
                resumeDeferredCatchupTimer = setTimeout(() => {
                    resumeDeferredCatchupTimer = null;
                    requestDeferredCatchupSnapshots();
                }, 150);
            }
        }
    }

    function handleVisibilityChange(): void {
        const isVisible = typeof document === 'undefined' || document.visibilityState !== 'hidden';

        if (isVisible) {
            if (hiddenSinceLastVisible) {
                const now = getNow();
                const hiddenWindowMs = hiddenAtMs == null ? 0 : Math.max(0, now - hiddenAtMs);
                startResumeSession(hiddenWindowMs);
                // If we were hidden, allow catch-up requests to re-fire even if prior snapshots were "pending".
                // This avoids getting stuck behind when a pending snapshot was dropped / never delivered.
                pendingSnapshotTopics.clear();
                workerBridge.reset('visibility_resume');
                // After resume, ignore assignment diffs until a snapshot arrives.
                awaitingAssignmentSnapshot = true;
                hiddenSinceLastVisible = false;
                hiddenAtMs = null;
            }
            if (visibilitySuspendTimer != null) {
                clearTimeout(visibilitySuspendTimer);
                visibilitySuspendTimer = null;
            }
            if (!socket || socket.readyState !== WebSocket.OPEN) {
                return;
            }
            applyVisibilityPolicy();
            return;
        }

        // When hidden, stop streaming analysis and proactively close the socket after a short delay.
        // NOTE: For single-user usage, we keep the WebSocket open while hidden to reduce resume latency.
        // Rendering work is coalesced elsewhere while hidden.
        hiddenSinceLastVisible = true;
        hiddenAtMs = getNow();
        applyVisibilityPolicy();
        if (visibilitySuspendTimer != null) {
            clearTimeout(visibilitySuspendTimer);
            visibilitySuspendTimer = null;
        }
    }

    function installVisibilityListener(): void {
        if (typeof document === 'undefined' || typeof document.addEventListener !== 'function') {
            return;
        }
        const handler = () => handleVisibilityChange();
        document.addEventListener('visibilitychange', handler);
        removeVisibilityListener = () => {
            document.removeEventListener('visibilitychange', handler);
            removeVisibilityListener = null;
        };
    }

    function installCardsListener(): void {
        if (removeCardsListener || !events?.on) {
            return;
        }
        const handler = () => {
            const activeWorkers = collectActiveWorkers(context);
            updateWorkerFilter(activeWorkers, SNAPSHOT_REASON_CARD_SUBSCRIBE);
            updateSubscriptions(SNAPSHOT_REASON_CARD_SUBSCRIBE);
        };
        const unsubscribe = events.on('live:cards-changed', handler);
        if (typeof unsubscribe === 'function') {
            removeCardsListener = unsubscribe;
        }
    }

    function installEngineLogListener(): void {
        if (removeEngineLogListener || !events?.on) {
            return;
        }
        const toggleHandler = (payload: unknown) => {
            if (!payload || typeof payload !== 'object') return;
            const data = payload as {
                gid?: unknown;
                role?: unknown;
                open?: unknown;
            };
            const gid = typeof data.gid === 'string' && data.gid.trim() ? data.gid.trim() : null;
            const role = data.role === 'black' || data.role === 'white' ? data.role : null;
            const open = data.open === true;
            if (!gid || !role) return;
            const topic = buildEngineLogDiffTopic(gid, role);
            if (open) {
                addEngineLogTopic(topic);
            } else {
                removeEngineLogTopic(topic);
            }
            updateSubscriptions(SNAPSHOT_REASON_CARD_SUBSCRIBE);
        };
        const switchHandler = (payload: unknown) => {
            if (!payload || typeof payload !== 'object') return;
            const data = payload as { prevGid?: unknown; nextGid?: unknown; role?: unknown };
            const prevGid = typeof data.prevGid === 'string' && data.prevGid.trim() ? data.prevGid.trim() : null;
            const nextGid = typeof data.nextGid === 'string' && data.nextGid.trim() ? data.nextGid.trim() : null;
            const role = data.role === 'black' || data.role === 'white' ? data.role : null;
            if (!role) return;
            if (prevGid) {
                removeEngineLogTopic(buildEngineLogDiffTopic(prevGid, role));
            }
            if (nextGid) {
                addEngineLogTopic(buildEngineLogDiffTopic(nextGid, role));
            }
            updateSubscriptions(SNAPSHOT_REASON_CARD_SUBSCRIBE);
        };
        const unsubToggle = events.on('live:engine-log-toggle', toggleHandler);
        const unsubSwitch = events.on('live:engine-log-switch', switchHandler);
        removeEngineLogListener = () => {
            if (typeof unsubToggle === 'function') unsubToggle();
            if (typeof unsubSwitch === 'function') unsubSwitch();
            removeEngineLogListener = null;
        };
    }

    function getNow(): number {
        if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
            return performance.now();
        }
        return Date.now();
    }

    function recordConnectSuccess(workerCount: number): void {
        const started = lastConnectStart ?? getNow();
        const duration = Math.max(0, getNow() - started);
        recordLiveDiagnosticsMetric('live.ws.connect', {
            triggered: 1,
            latency_ms: duration,
            workers: workerCount,
        });
    }

    function recordConnectFailure(): void {
        recordLiveDiagnosticsMetric('live.ws.connect', { failed: 1 });
    }

    // Expose diagnostics function globally for debugging from browser console
    const requestWorkerDiagnostics = () => {
        workerBridge?.requestDiagnostics();
    };
    if (typeof window !== 'undefined') {
        (window as unknown as { __liveDiagnostics?: () => void }).__liveDiagnostics = requestWorkerDiagnostics;
    }

    /**
     * Handle internal tab activation (switching to Live tab from another dashboard tab).
     * Unlike visibility resume, the WebSocket is already open and we just need to catch up.
     * Strategy:
     * 1. Request assignment snapshot first (need gids before requesting game snapshots)
     * 2. When assignment snapshot arrives, request game snapshots with forceLatest=true
     * This ensures we get the latest state without replaying diffs.
     */
    function onTabActivate(): void {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
        }
        // Skip during initial load - bootstrap handles the first snapshot fetch.
        // Only trigger on subsequent tab switches after we've received at least one assignment.
        if (!hasReceivedInitialAssignment) {
            return;
        }
        const isVisible = typeof document === 'undefined' || document.visibilityState !== 'hidden';
        if (!isVisible) {
            // Browser tab is hidden; skip until visibility resume handles it
            return;
        }

        tabResumeToken = resumeCoordinator.begin('tab');
        tabResumeAwaitingSnapshot = true;
        if (tabResumeForceEndTimer != null) {
            clearTimeout(tabResumeForceEndTimer);
        }
        tabResumeForceEndTimer = setTimeout(() => {
            tabResumeForceEndTimer = null;
            if (tabResumeToken) {
                resumeCoordinator.end(tabResumeToken);
                tabResumeToken = null;
                tabResumeAwaitingSnapshot = false;
            }
        }, 2000);
        recordLiveDiagnosticsMetric('live.ws.tab_resume', {
            triggered: 1,
            active_workers: collectActiveWorkers(context).size,
        });

        // Reset merge worker state to drop pending diffs and wait for fresh snapshots
        workerBridge.reset('tab_resume');

        // Clear pending state and wait for assignment snapshot
        pendingSnapshotTopics.clear();
        awaitingAssignmentSnapshot = true;
        // Set flag to request game snapshots after assignment snapshot arrives
        awaitingTabResumeGameSnapshots = true;
        assignmentByWorker.clear();
        lastSnapshotRequestAtByTopic.delete('live.assignment.snapshot');

        // Request latest assignment snapshot (no replay)
        // Game snapshots will be requested in the assignment snapshot handler
        requestSnapshot('live.assignment.snapshot', null, SNAPSHOT_REASON_TAB_RESUME);
    }

    return {
        start,
        stop,
        isActive,
        onTabActivate,
    };
}
