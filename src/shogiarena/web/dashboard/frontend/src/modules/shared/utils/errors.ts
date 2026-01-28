export class DashboardFatalError extends Error {
    constructor(message: string, options?: ErrorOptions) {
        super(message, options);
        this.name = 'DashboardFatalError';
    }
}

export function formatErrorMessage(error: unknown): string {
    if (error instanceof Error) {
        return error.message;
    }
    return typeof error === 'string' ? error : JSON.stringify(error, null, 2);
}

export function crash<K extends string>(context: K, error: unknown): never {
    const detail = formatErrorMessage(error);
    const message = `[Dashboard Fatal] ${context}: ${detail}`;
    throw new DashboardFatalError(message, { cause: error });
}

export interface DashboardRecoverableFailureOptions {
    readonly scope: string;
    readonly userMessage?: string;
}

/**
 * ダッシュボード全体で使用する「recoverable failure」用のイベント名。
 * Live / SPSA / Tournament / Games などのモジュールは、このイベントを通じて
 * Diagnostics パネルや統一的なログに失敗情報を流すことを想定しています。
 */
export const DASHBOARD_RECOVERABLE_FAILURE_EVENT = 'dashboard:recoverable-failure';

/**
 * ダッシュボード向けの recoverable failure を一元的に報告するユーティリティ。
 *
 * - scope には `SpsaStreams.Correlation` や `Live.WorkerUpdate` のようなプレフィックス付き識別子を渡します。
 * - userMessage はユーザー向けに表示する簡潔なメッセージであり、詳細なスタックトレースは `error` に保持します。
 * - この関数自身は例外を投げず、「報告のみ」を担当します。
 *
 * 実際の UI 表示（Notice やバナー）や fatal へのエスカレーションは、呼び出し元（もしくは
 * DASHBOARD_RECOVERABLE_FAILURE_EVENT リスナー）が責務を持ちます。
 */
export function reportDashboardRecoverableFailure(error: unknown, options: DashboardRecoverableFailureOptions): void {
    const { scope, userMessage } = options;
    const owner = (typeof window !== 'undefined' ? window : undefined) as
        | (Window & {
              DashboardCore?: { events?: { emit?: (name: string, payload: unknown) => void } };
              CustomEvent?: typeof CustomEvent;
          })
        | undefined;
    const detail = {
        scope,
        message: formatErrorMessage(error),
        userMessage: userMessage ?? null,
        error,
        ts: Date.now(),
    };
    try {
        // DashboardCore 内部のイベントバス向け
        owner?.DashboardCore?.events?.emit?.(DASHBOARD_RECOVERABLE_FAILURE_EVENT, detail);
        // DOM (window) 向けに CustomEvent も発火し、Live Diagnostics HUD などから購読できるようにする
        const CustomEventCtor =
            (owner && typeof owner.CustomEvent === 'function' && owner.CustomEvent) ||
            (typeof CustomEvent === 'function' ? CustomEvent : null);
        if (owner && typeof owner.dispatchEvent === 'function' && CustomEventCtor) {
            const event = new CustomEventCtor(DASHBOARD_RECOVERABLE_FAILURE_EVENT, { detail });
            owner.dispatchEvent(event);
        }
    } catch {
        // Diagnostics への通知に失敗しても、ここでは握りつぶす。
        // 呼び出し元側で必要に応じて個別のログを行うこと。
    }
}
