import { formatErrorMessage } from '@/modules/shared/utils/errors';

const NETWORK_ERROR_CODES = new Set([
    'ECONNRESET',
    'ECONNREFUSED',
    'ENETDOWN',
    'ENETUNREACH',
    'EHOSTDOWN',
    'EHOSTUNREACH',
]);

const NETWORK_ERROR_KEYWORDS = [
    'failed to fetch',
    'networkerror',
    'network error',
    'connection reset',
    'connection refused',
    'err_empty_response',
    'err_connection',
];

export function isAbortError(error: unknown): error is DOMException {
    return error instanceof DOMException && error.name === 'AbortError';
}

export function describeError(error: unknown): string {
    if (error instanceof Error && error.message) {
        return error.message;
    }
    if (typeof error === 'string') {
        return error;
    }
    return formatErrorMessage(error);
}

export function isLikelyNetworkError(error: unknown): boolean {
    if (!error) {
        return false;
    }
    if (isAbortError(error)) {
        return false;
    }
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        return true;
    }
    const maybeCode = typeof error === 'object' && error !== null ? (error as { code?: unknown }).code : null;
    if (typeof maybeCode === 'string' && NETWORK_ERROR_CODES.has(maybeCode.toUpperCase())) {
        return true;
    }
    if (error instanceof TypeError && /fetch/i.test(error.message)) {
        return true;
    }
    if (error instanceof Error && typeof error.message === 'string') {
        const lowered = error.message.toLowerCase();
        if (NETWORK_ERROR_KEYWORDS.some((keyword) => lowered.includes(keyword))) {
            return true;
        }
    }
    if (typeof error === 'string') {
        const lowered = error.toLowerCase();
        if (NETWORK_ERROR_KEYWORDS.some((keyword) => lowered.includes(keyword))) {
            return true;
        }
    }
    return false;
}
