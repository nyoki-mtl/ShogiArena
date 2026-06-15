import type { LiveViewSnapshot } from '@/modules/live/types';
import { LIVE_VIEW_UPDATE_EVENT } from '@/modules/shared/stores/live-view-snapshot';
import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';
import type { DashboardCore } from '@/types/dashboard';

/**
 * Copy helper used by live updates.
 *
 * 優先順位:
 * 1) ネイティブ structuredClone
 * 2) JSON ベースのディープコピー（循環参照なしの JSON 互換 payload 向け）
 * 3) 配列スプレッド / オブジェクト浅いコピー（最終手段。日付や Map は壊れるが UI 表示は継続）
 *
 * いずれも失敗した場合は Diagnostics に通知した上で fail-fast し、
 * ライブ更新カードの更新を諦めさせる（Notice/Diagnostics で再読込を促す）方針。
 */
export function createSafeClone(warnSoftFailure: DashboardCore['warnSoftFailure']) {
    const scope = 'Live.SafeClone';

    function cloneViaJson<T>(value: T): T {
        return JSON.parse(JSON.stringify(value)) as T;
    }

    function cloneShallow<T>(value: T): T {
        if (value == null) return value;
        if (Array.isArray(value)) return [...value] as unknown as T;
        if (typeof value === 'object') return { ...(value as Record<string, unknown>) } as unknown as T;
        return value;
    }

    function safeClone<T>(value: T): T {
        if (value == null) {
            return value;
        }

        if (typeof structuredClone === 'function') {
            try {
                return structuredClone(value);
            } catch (error) {
                reportDashboardRecoverableFailure(error, {
                    scope: `${scope}.StructuredClone`,
                    userMessage: 'ブラウザの structuredClone でコピーに失敗したため代替手段に切り替えました。',
                });
            }
        } else {
            reportDashboardRecoverableFailure(new Error('structuredClone is not available'), {
                scope: `${scope}.Unavailable`,
                userMessage: 'structuredClone 非対応ブラウザのため、互換モードのコピーに切り替えました。',
            });
        }

        try {
            return cloneViaJson(value);
        } catch (error) {
            reportDashboardRecoverableFailure(error, {
                scope: `${scope}.JsonFallback`,
                userMessage: 'JSON 互換でのコピーに失敗したため浅いコピーに切り替えます。',
            });
        }

        try {
            return cloneShallow(value);
        } catch (error) {
            reportDashboardRecoverableFailure(error, {
                scope: `${scope}.FallbackExhausted`,
                userMessage: 'ライブ更新データのコピーに失敗しました。再読込で復旧してください。',
            });
            // fail-fast: ここでクラッシュさせ、ライブカードの更新を停止してユーザーに気付かせる
            return warnSoftFailure(scope, error);
        }
    }

    return safeClone;
}

interface ApplyLiveViewArgs {
    state: DashboardCore['state'];
    liveView: LiveViewSnapshot | null | undefined;
    source: 'summary';
    safeClone: <T>(value: T) => T;
    emitEvent: (eventName: string, payload: unknown) => void;
}

export function applyLiveViewSnapshotIfPresent({
    state,
    liveView,
    source,
    safeClone,
    emitEvent,
}: ApplyLiveViewArgs): void {
    if (liveView === undefined) {
        return;
    }
    const runtimeMode = state.runtimeMode ?? 'unknown';
    if (runtimeMode === 'match' || runtimeMode === 'sprt') {
        if (liveView === null) {
            return;
        }
        const incomingMode = liveView?.mode ?? 'unknown';
        if (incomingMode !== 'unknown' && incomingMode !== runtimeMode) {
            return;
        }
    }
    if (liveView === null) {
        state.liveViewSnapshot = null;
        emitEvent(LIVE_VIEW_UPDATE_EVENT, null);
        return;
    }
    const snapshot = safeClone(liveView);
    state.liveViewSnapshot = snapshot;
    emitEvent(LIVE_VIEW_UPDATE_EVENT, safeClone(snapshot));
    if (liveView.progress) {
        emitEvent('live:progress', {
            source,
            mode: liveView.mode,
            progress: safeClone(liveView.progress),
        });
    }
}
