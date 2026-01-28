export type HydrationTrigger = string;

export interface DeferredHydratorOptions {
    readonly hydrate: (trigger?: HydrationTrigger) => Promise<void> | void;
    readonly delayMs?: number;
    readonly onError?: (error: unknown, trigger?: HydrationTrigger) => void;
    readonly onComplete?: (trigger?: HydrationTrigger) => void;
}

export interface DeferredHydrator {
    ensure: (trigger?: HydrationTrigger, options?: { immediate?: boolean }) => void;
    reset: () => void;
    markDirty: () => void;
    isHydrated: () => boolean;
}

export function createDeferredHydrator({
    hydrate,
    delayMs = 0,
    onError,
    onComplete,
}: DeferredHydratorOptions): DeferredHydrator {
    let hydrated = false;
    let timerId: number | null = null;
    let inFlight: Promise<void> | null = null;

    const clearTimer = (): void => {
        if (timerId !== null) {
            clearTimeout(timerId);
            timerId = null;
        }
    };

    const runHydration = (trigger?: HydrationTrigger): void => {
        inFlight = Promise.resolve()
            .then(() => hydrate(trigger))
            .then(() => {
                hydrated = true;
                onComplete?.(trigger);
            })
            .catch((error) => {
                hydrated = false;
                onError?.(error, trigger);
            })
            .finally(() => {
                inFlight = null;
            });
    };

    return {
        ensure: (trigger?: HydrationTrigger, options?: { immediate?: boolean }) => {
            if (hydrated || inFlight) {
                return;
            }
            if (options?.immediate) {
                clearTimer();
                runHydration(trigger);
                return;
            }
            if (timerId !== null) {
                return;
            }
            timerId = window.setTimeout(
                () => {
                    timerId = null;
                    runHydration(trigger);
                },
                Math.max(0, delayMs),
            );
        },
        reset: () => {
            hydrated = false;
            clearTimer();
            inFlight = null;
        },
        markDirty: () => {
            hydrated = false;
        },
        isHydrated: () => hydrated,
    };
}
