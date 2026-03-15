import { DASHBOARD_RECOVERABLE_FAILURE_EVENT } from '@/modules/shared/utils/errors';
import type { LiveUpdatesContext } from '@/modules/live/types/updates';
import type { DashboardCore } from '@/types/dashboard';

type RecoverableFailureDetail = {
    scope: string;
    message?: string | null;
    userMessage?: string | null;
};

const NOTICE_SUPPRESS_MS = 15_000;

function shouldNotify(scope: string): boolean {
    return scope.startsWith('Live.SafeClone') || scope.startsWith('Live.WorkerUpdate');
}

export function installRecoverableFailureNotices(context: LiveUpdatesContext): void {
    const { events, core } = context;
    const showNotice: DashboardCore['showNotice'] | undefined = core?.showNotice?.bind?.(core);
    if (!events || typeof events.on !== 'function' || typeof showNotice !== 'function') {
        return;
    }

    const lastNotified = new Map<string, number>();

    const handler = (detail: RecoverableFailureDetail | null | undefined): void => {
        if (!detail || typeof detail.scope !== 'string') {
            return;
        }
        if (!shouldNotify(detail.scope)) {
            return;
        }
        const now = Date.now();
        const last = lastNotified.get(detail.scope) ?? 0;
        if (now - last < NOTICE_SUPPRESS_MS) {
            return; // avoid spamming when multiple events fire rapidly
        }
        lastNotified.set(detail.scope, now);

        const message = detail.userMessage || detail.message || `${detail.scope}: recoverable failure detected.`;
        showNotice(message, 'warn', { timeout: 8000 });
    };

    const dispose = events.on(DASHBOARD_RECOVERABLE_FAILURE_EVENT, handler);

    // ベースライフサイクルはページ存続＝モジュール存続前提。必要になればここで dispose を export して呼び出す。
    if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
        window.addEventListener('beforeunload', () => {
            try {
                dispose?.();
            } catch {
                // ignore
            }
        });
    }
}
