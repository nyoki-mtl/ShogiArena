import type { SpsaRefreshOptions } from '@/modules/spsa/types';
import {
    consumeQueuedRefreshRequest,
    getRefreshPromise,
    isRefreshInFlight,
    queueRefreshRequest,
    setRefreshPromise,
} from '../state';

type RefreshQueueDeps = {
    performRefresh: (options: SpsaRefreshOptions) => Promise<void>;
};

export type RefreshQueue = {
    refreshAll: (options?: SpsaRefreshOptions) => Promise<void>;
};

export function createRefreshQueue({ performRefresh }: RefreshQueueDeps): RefreshQueue {
    const refreshAll = async (options: SpsaRefreshOptions = {}): Promise<void> => {
        if (isRefreshInFlight()) {
            queueRefreshRequest(options);
            const existingPromise = getRefreshPromise();
            return existingPromise ?? Promise.resolve();
        }

        const executeQueue = async (initialOptions: SpsaRefreshOptions): Promise<void> => {
            let nextOptions: SpsaRefreshOptions | null = initialOptions;
            while (nextOptions) {
                await performRefresh(nextOptions);
                nextOptions = consumeQueuedRefreshRequest();
            }
        };

        const queuePromise = executeQueue(options ?? {});
        const trackedPromise = queuePromise
            .catch((error) => {
                throw error;
            })
            .finally(() => {
                setRefreshPromise(null);
            });

        setRefreshPromise(trackedPromise);
        return trackedPromise;
    };

    return { refreshAll };
}
