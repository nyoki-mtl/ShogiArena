import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';

type GuardDeps = {
    emit?: (eventName: string, payload: unknown) => void;
    notifyDashboardServerStopped: () => void;
    closeActive: (reason: 'user' | 'system') => void;
};

export function createSseContractGuard(deps: GuardDeps) {
    const { emit, notifyDashboardServerStopped, closeActive } = deps;

    return function handleContractViolation(context: string, error: unknown): never {
        emit?.('live:sse-contract-violation', { context, error });
        notifyDashboardServerStopped();
        closeActive('system');
        const err = error instanceof Error ? error : new Error(String(error));
        reportDashboardRecoverableFailure(err, {
            scope: `Live.SSE.Contract.${context}`,
            userMessage: 'ライブ更新ストリームが想定外のデータを受信したため停止しました。',
        });
        throw err;
    };
}
