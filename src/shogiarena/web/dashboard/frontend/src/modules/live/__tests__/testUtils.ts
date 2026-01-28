import { vi } from 'vitest';
import { JSDOM } from 'jsdom';

import type { LiveCardsApi, LiveSummaryApi, LiveTimeApi, LiveUpdatesApi } from '@/types/live';
import { registerLiveApi, resetLiveNamespace, type LiveNamespaceOwner } from '@/modules/live/utils/liveNamespace';

export type TestOwnerHandle = {
    owner: LiveNamespaceOwner;
    restore: () => void;
};

export function createLiveTestOwner(options?: {
    wsDiagnostics?: unknown;
    owner?: LiveNamespaceOwner;
}): TestOwnerHandle {
    const owner = (options?.owner ?? (window as unknown as LiveNamespaceOwner)) as LiveNamespaceOwner;
    const originalFetch = owner.fetch;
    const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => options?.wsDiagnostics ?? {},
    });
    owner.fetch = fetchMock as typeof fetch;

    return {
        owner,
        restore: () => {
            if (originalFetch) {
                owner.fetch = originalFetch;
            } else {
                // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
                delete (owner as unknown as Record<string, unknown>).fetch;
            }
        },
    };
}

export function createJSDOMOwner(html: string): {
    window: Window;
    document: Document;
    owner: LiveNamespaceOwner;
    restoreGlobals: () => void;
} {
    const dom = new JSDOM(html);
    const { window } = dom;
    const previousWindow = globalThis.window;
    const previousDocument = globalThis.document;
    const previousCSS = (globalThis as typeof globalThis & { CSS?: { escape?: (value: string) => string } }).CSS;

    Object.assign(globalThis, { window, document: window.document });

    const escapePolyfill = (value: string) => value;
    if (!window.CSS) {
        (window as typeof window & { CSS: { escape: (value: string) => string } }).CSS = {
            escape: escapePolyfill,
        };
    } else if (typeof window.CSS.escape !== 'function') {
        window.CSS.escape = escapePolyfill;
    }
    (globalThis as typeof globalThis & { CSS?: { escape: (value: string) => string } }).CSS = window.CSS ?? {
        escape: escapePolyfill,
    };

    const restoreGlobals = () => {
        if (previousWindow) {
            (globalThis as typeof globalThis).window = previousWindow;
        } else {
            // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
            delete (globalThis as typeof globalThis & { window?: Window }).window;
        }
        if (previousDocument) {
            (globalThis as typeof globalThis).document = previousDocument;
        } else {
            // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
            delete (globalThis as typeof globalThis & { document?: Document }).document;
        }
        if (previousCSS) {
            (globalThis as typeof globalThis & { CSS?: { escape?: (value: string) => string } }).CSS = previousCSS;
        } else {
            // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
            delete (globalThis as typeof globalThis & { CSS?: { escape?: (value: string) => string } }).CSS;
        }
    };

    return { window, document: window.document, owner: window as unknown as LiveNamespaceOwner, restoreGlobals };
}

export function resetTestDom(doc: Document, html = ''): void {
    doc.body.innerHTML = html;
}

export function removeElementById(doc: Document, id: string): void {
    doc.getElementById(id)?.remove();
}

export function resetDiagnosticsDom(doc: Document): void {
    resetTestDom(doc);
    removeElementById(doc, 'dashboardLiveDiagnosticsPanelStyles');
}

export function stubLiveUpdatesGlobals(): void {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
        cb(0);
        return 0;
    });

    vi.stubGlobal(
        'Worker',
        class MockWorker {
            addEventListener() {}
            removeEventListener() {}
            postMessage() {}
            terminate() {}
        } as unknown as typeof Worker,
    );

    const wsCtor = vi.fn(() => ({
        readyState: 0,
        close: vi.fn(),
        send: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
    }));
    (wsCtor as unknown as { OPEN?: number; CONNECTING?: number; CLOSING?: number; CLOSED?: number }).OPEN = 1;
    (wsCtor as unknown as { OPEN?: number; CONNECTING?: number; CLOSING?: number; CLOSED?: number }).CONNECTING = 0;
    (wsCtor as unknown as { OPEN?: number; CONNECTING?: number; CLOSING?: number; CLOSED?: number }).CLOSING = 2;
    (wsCtor as unknown as { OPEN?: number; CONNECTING?: number; CLOSING?: number; CLOSED?: number }).CLOSED = 3;
    vi.stubGlobal('WebSocket', wsCtor as unknown as typeof WebSocket);
}

interface LiveNamespaceOverrides {
    cards?: Partial<LiveCardsApi>;
    time?: Partial<LiveTimeApi>;
    summary?: Partial<LiveSummaryApi>;
    updates?: Partial<LiveUpdatesApi>;
}

export function setupLiveNamespace(
    overrides: LiveNamespaceOverrides = {},
    owner: LiveNamespaceOwner = window as unknown as LiveNamespaceOwner,
): void {
    if (overrides.time) {
        registerLiveApi(owner, 'time', overrides.time as LiveTimeApi, { provider: 'test:live/time' });
    }
    if (overrides.cards) {
        const baseCards: LiveCardsApi = {
            initializeCards: () => {
                /* noop stub for tests */
            },
            updateWorkerOptionLabels: () => {
                throw new Error('LiveCardsApi.updateWorkerOptionLabels stub not provided');
            },
            computeAutoViewPly: () => {
                throw new Error('LiveCardsApi.computeAutoViewPly stub not provided');
            },
            syncWorkerViewFromCard: () => {
                throw new Error('LiveCardsApi.syncWorkerViewFromCard stub not provided');
            },
            populateSourceDropdown: async () => {
                throw new Error('LiveCardsApi.populateSourceDropdown stub not provided');
            },
            focusGameOnLiveTab: async () => {
                throw new Error('LiveCardsApi.focusGameOnLiveTab stub not provided');
            },
        };
        const merged: LiveCardsApi = {
            ...baseCards,
            ...(overrides.cards as LiveCardsApi),
        };
        if (typeof merged.initializeCards !== 'function') {
            merged.initializeCards = baseCards.initializeCards;
        }
        registerLiveApi(owner, 'cards', merged, { provider: 'test:live/cards' });
    }
    if (overrides.summary) {
        registerLiveApi(owner, 'summary', overrides.summary as LiveSummaryApi, { provider: 'test:live/summary' });
    }
    if (overrides.updates) {
        registerLiveApi(owner, 'updates', overrides.updates as LiveUpdatesApi, { provider: 'test:live/updates' });
    }
}

export function teardownLiveNamespace(owner: LiveNamespaceOwner = window as unknown as LiveNamespaceOwner): void {
    resetLiveNamespace(owner);
}
