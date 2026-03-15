import { RESULT_DETAIL_LABELS } from './viewmodel.constants';

export function formatTitleCase(value: string): string {
    return value
        .split(/\s+/)
        .filter(Boolean)
        .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
        .join(' ');
}

export function formatDetailLabel(detail: string): string {
    const trimmed = detail.trim();
    if (!trimmed) return '';
    const mapped = RESULT_DETAIL_LABELS[trimmed.toLowerCase()];
    if (mapped) return mapped;
    return formatTitleCase(trimmed);
}

export function formatTimestamp(iso: string | null): { label: string | null; title: string | null } {
    if (typeof iso !== 'string' || !iso) {
        return { label: null, title: null };
    }
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) {
        return { label: iso, title: null };
    }
    const pad = (value: number) => String(value).padStart(2, '0');
    const label = `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`;
    return { label, title: parsed.toISOString() };
}
