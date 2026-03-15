import type { JsonObject } from '@/types/shared';

const OPTION_SOURCE_LABEL_MAP: Record<string, string> = {
    config: 'Engine config',
    override: 'Tournament override',
    eval: 'Eval config',
    overlay: 'Overlay preset',
    rules: 'Rules default',
};

function hasOwnJsonValue(record: JsonObject | undefined, key: string): record is JsonObject {
    return !!record && typeof record === 'object' && Object.hasOwn(record, key);
}

export function resolveOptionSourceLabel(
    key: string,
    sourceMap: JsonObject | undefined,
    detailMap: JsonObject | undefined,
): string {
    if (hasOwnJsonValue(detailMap, key)) {
        const value = detailMap[key];
        if (value !== undefined && value !== null && value !== '') {
            return String(value);
        }
    }

    if (!hasOwnJsonValue(sourceMap, key)) {
        return '-';
    }

    const normalized = String(sourceMap[key]);
    if (!normalized) {
        return '-';
    }

    return OPTION_SOURCE_LABEL_MAP[normalized] ?? normalized;
}
