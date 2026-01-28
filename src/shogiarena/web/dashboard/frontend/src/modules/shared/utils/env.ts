export function isTestEnvironment(): boolean {
    const hasImportMetaTestFlag =
        typeof import.meta !== 'undefined' &&
        !!((import.meta as ImportMeta & { vitest?: boolean }).vitest || import.meta.env?.MODE === 'test');

    const globalScope =
        typeof globalThis !== 'undefined'
            ? (globalThis as { process?: { env?: Record<string, unknown> }; vi?: unknown })
            : undefined;

    const hasProcessTestFlag =
        typeof globalThis !== 'undefined' && typeof globalScope?.process?.env?.VITEST_WORKER_ID === 'string';

    const hasGlobalViFlag = typeof globalScope?.vi !== 'undefined';

    const hasNodeEnvTestFlag =
        typeof globalScope?.process?.env?.NODE_ENV === 'string' && globalScope.process.env.NODE_ENV === 'test';

    return hasImportMetaTestFlag || hasProcessTestFlag || hasGlobalViFlag || hasNodeEnvTestFlag;
}
