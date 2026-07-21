import type { DashboardShared, DashboardSharedApi } from '@/types/dashboard';
import type { JsonRequestOptions, RequestError } from '@/types/shared';

declare const window: Window & typeof globalThis;

function sanitizeTrailingSlash(url: string): string {
    return url.replace(/\/$/, '');
}

function getDefaultApiPort(): number {
    const configured = Number(window.ARENA_API_PORT);
    if (Number.isFinite(configured) && configured > 0) {
        return configured;
    }
    throw new Error('Failed to resolve dashboard API port: ARENA_API_PORT is not configured');
}

export function resolveApiBase(): string {
    const port = getDefaultApiPort();
    const query = new URLSearchParams(window.location?.search ?? '');
    const explicit = query.get('api_base');
    if (explicit) {
        return sanitizeTrailingSlash(explicit);
    }

    if (window.DASHBOARD_API_BASE) {
        return sanitizeTrailingSlash(String(window.DASHBOARD_API_BASE));
    }

    const location = window.location;
    if (!location) {
        throw new Error('Failed to resolve dashboard API base URL: window.location is not available');
    }

    if (location.protocol === 'file:') {
        // ダッシュボードは same-origin 限定なので、file:// から localhost を叩くと
        // すべてのリクエストが 403 になる。原因の分かる形で先に失敗させる。
        throw new Error(
            `Failed to resolve dashboard API base URL: open http://localhost:${port} instead of the local file. ` +
                'The dashboard only accepts same-origin requests.',
        );
    }

    if (location.origin === 'null') {
        throw new Error('Failed to resolve dashboard API base URL: location.origin is null (sandbox environment)');
    }

    return sanitizeTrailingSlash(location.origin);
}

function getApiBase(): string {
    const base = resolveApiBase();
    if (!base) {
        throw new Error('Failed to resolve dashboard API base URL');
    }
    return base;
}

function isBodyInit(value: unknown): value is BodyInit {
    if (value == null) {
        return false;
    }
    if (typeof value === 'string') {
        return true;
    }
    if (typeof Blob !== 'undefined' && value instanceof Blob) {
        return true;
    }
    if (typeof FormData !== 'undefined' && value instanceof FormData) {
        return true;
    }
    if (typeof URLSearchParams !== 'undefined' && value instanceof URLSearchParams) {
        return true;
    }
    if (value instanceof ArrayBuffer) {
        return true;
    }
    if (ArrayBuffer.isView(value)) {
        return true;
    }
    if (typeof ReadableStream !== 'undefined' && value instanceof ReadableStream) {
        return true;
    }
    return false;
}

export async function requestJson<T = unknown>(url: string, options: JsonRequestOptions = {}): Promise<T> {
    const { body: optionBody, headers, validate, ...rest } = options;
    const init: RequestInit = { ...rest };
    const method = (init.method ?? 'GET').toUpperCase();
    const requiresJsonContentType = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);

    if (optionBody !== undefined) {
        init.body = isBodyInit(optionBody) ? optionBody : JSON.stringify(optionBody);
    }

    if (headers) {
        const normalizedHeaders = new Headers(headers);
        if (requiresJsonContentType && !normalizedHeaders.has('Content-Type')) {
            normalizedHeaders.set('Content-Type', 'application/json');
        }
        init.headers = normalizedHeaders;
    } else if (requiresJsonContentType || (init.body && !isBodyInit(optionBody))) {
        init.headers = { 'Content-Type': 'application/json' };
    }

    const hasInitOptions = Object.keys(init).length > 0;

    const response = hasInitOptions ? await fetch(url, init) : await fetch(url);

    if (!response.ok) {
        const error = new Error(response.statusText || 'Request failed') as RequestError;
        error.status = response.status;
        try {
            error.payload = await response.json();
        } catch {
            error.payload = null;
        }
        throw error;
    }

    if (response.status === 204) {
        if (validate) {
            validate(null);
        }
        return null as T;
    }

    let payload: unknown;
    try {
        payload = await response.json();
    } catch (error) {
        const parseError = new Error('Failed to parse response JSON') as RequestError;
        parseError.status = response.status;
        parseError.cause = error;
        throw parseError;
    }

    if (validate) {
        validate(payload);
    }

    return payload as T;
}

let installedApi: DashboardSharedApi | null = null;

function attachApiToWindow(target: Window & typeof globalThis): void {
    const shared: DashboardShared = target.DashboardShared ?? {};
    target.DashboardShared = shared;
    shared.api = installedApi ?? undefined;
}

export function installDashboardApi(target: Window & typeof globalThis = window): DashboardSharedApi {
    if (installedApi) {
        attachApiToWindow(target);
        return installedApi;
    }

    const api: DashboardSharedApi = {
        getDefaultApiPort,
        resolveApiBase,
        getApiBase,
    };

    installedApi = api;
    attachApiToWindow(target);
    return api;
}
