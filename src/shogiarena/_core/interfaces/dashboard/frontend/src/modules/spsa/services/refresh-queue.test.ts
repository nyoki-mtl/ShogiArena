import { afterEach, describe, expect, it, vi } from 'vitest';
import { consumeQueuedRefreshRequest, setRefreshPromise } from '../state';
import { createRefreshQueue } from './refresh-queue';

function deferred(): { promise: Promise<void>; resolve: () => void; reject: (error: Error) => void } {
    let resolve!: () => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<void>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

afterEach(() => {
    consumeQueuedRefreshRequest();
    setRefreshPromise(null);
});

describe('refresh queue', () => {
    it('retains the strongest force option across concurrent callers', async () => {
        const first = deferred();
        const seen: Array<boolean | undefined> = [];
        const performRefresh = vi.fn(async (options: { force?: boolean }) => {
            seen.push(options.force);
            if (seen.length === 1) {
                await first.promise;
            }
        });
        const queue = createRefreshQueue({ performRefresh });

        const running = queue.refreshAll({ force: false });
        const queued = queue.refreshAll({ force: true });
        void queue.refreshAll({ force: false });
        first.resolve();
        await Promise.all([running, queued]);

        expect(seen).toEqual([false, true]);
    });

    it('drains a stronger queued request even when the active refresh fails', async () => {
        const first = deferred();
        const failure = new Error('initial refresh failed');
        const seen: Array<boolean | undefined> = [];
        const performRefresh = vi.fn(async (options: { force?: boolean }) => {
            seen.push(options.force);
            if (seen.length === 1) {
                await first.promise;
            }
        });
        const queue = createRefreshQueue({ performRefresh });

        const running = queue.refreshAll({ force: false });
        const queued = queue.refreshAll({ force: true });
        first.reject(failure);

        await expect(running).rejects.toBe(failure);
        await expect(queued).rejects.toBe(failure);
        expect(seen).toEqual([false, true]);
    });
});
