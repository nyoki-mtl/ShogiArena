import {
    formatDuration,
    formatNodesCountDetail,
    formatNodesCountShort,
    parseTimeControlSpec,
} from '@/modules/shared/utils/time-control';

export function formatOptionValue(value: unknown): string {
    if (value === null || value === undefined) return '-';
    if (Array.isArray(value)) {
        return value.map((item) => formatOptionValue(item)).join(', ');
    }
    if (typeof value === 'object') {
        try {
            return JSON.stringify(value);
        } catch (_error) {
            return String(value);
        }
    }
    return String(value);
}

export { parseTimeControlSpec, formatDuration, formatNodesCountShort, formatNodesCountDetail };
