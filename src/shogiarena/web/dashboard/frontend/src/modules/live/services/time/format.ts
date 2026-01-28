import { parseTimeControlSpec } from './parse';
import type { ParsedTimeControlSpec } from '@/modules/live/types/time';

export function formatRemain(ms: number | string | null | undefined): string {
    const totalMs = Math.max(0, Number(ms ?? 0));
    const totalSeconds = Math.floor(totalMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    const tenths = Math.floor((totalMs % 1000) / 100);
    if (minutes >= 60) {
        const hours = Math.floor(minutes / 60);
        const remMinutes = minutes % 60;
        return `${hours}:${String(remMinutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }
    if (minutes >= 1) {
        return `${minutes}:${String(seconds).padStart(2, '0')}`;
    }
    return `${String(seconds).padStart(2, '0')}.${tenths}`;
}

export function formatInc(ms: number | string | null | undefined): string {
    const totalMs = Math.max(0, Number(ms ?? 0));
    if (totalMs < 1000) {
        const tenths = Math.floor(totalMs / 100);
        return `${(tenths / 10).toFixed(1)}s`;
    }
    const totalSeconds = Math.floor(totalMs / 1000);
    if (totalSeconds < 60) {
        return `${totalSeconds}s`;
    }
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (seconds === 0) return `${minutes}m`;
    return `${minutes}m${seconds}s`;
}

export function formatCountUp(ms: number | string | null | undefined): string {
    const totalMs = Math.max(0, Number(ms ?? 0));
    const totalSeconds = Math.floor(totalMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    const tenths = Math.floor((totalMs % 1000) / 100);
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${tenths}`;
}

export function formatByoyomi(ms: number | string | null | undefined): string {
    const totalMs = Math.max(0, Number(ms ?? 0));
    const seconds = Math.floor(totalMs / 1000);
    const tenths = Math.floor((totalMs % 1000) / 100);
    return `${String(seconds).padStart(2, '0')}.${tenths}`;
}

function formatSI(value: number): string {
    if (value >= 1e9) return `${(value / 1e9).toFixed(value % 1e9 ? 1 : 0)}G`;
    if (value >= 1e6) return `${(value / 1e6).toFixed(value % 1e6 ? 1 : 0)}M`;
    if (value >= 1e3) return `${(value / 1e3).toFixed(value % 1e3 ? 1 : 0)}K`;
    return `${value}`;
}

export function formatTimeControlShort(spec: ParsedTimeControlSpec | string | null | undefined): string {
    let tc: ParsedTimeControlSpec;
    if (typeof spec === 'string') {
        tc = parseTimeControlSpec(spec);
    } else if (spec == null) {
        tc = parseTimeControlSpec(null);
    } else {
        tc = spec;
    }
    if (tc.mode === 'fixed' && tc.fixedMs) {
        return `${formatInc(tc.fixedMs)} fixed`;
    }
    if (tc.mode === 'search') {
        const depthPart = tc.depth != null ? `d${tc.depth}` : null;
        const nodesPart = tc.nodes != null ? `${formatSI(tc.nodes)} nodes` : null;
        return [depthPart, nodesPart].filter(Boolean).join(' ') || 'search-only';
    }
    const pieces: string[] = [];
    if (tc.initial > 0) {
        const minutes = Math.floor(tc.initial / 60);
        const seconds = tc.initial % 60;
        if (minutes > 0) {
            pieces.push(`${minutes}m${seconds > 0 ? `${seconds}s` : ''}`.trim());
        } else {
            pieces.push(`${seconds}s`);
        }
    }
    if (tc.increment > 0) {
        pieces.push(`+${tc.increment}s`);
    } else if (tc.byoyomi > 0) {
        pieces.push(`+b${tc.byoyomi}s`);
    }
    if (tc.depth != null) {
        pieces.push(`d${tc.depth}`);
    }
    if (tc.nodes != null) {
        pieces.push(`${formatSI(tc.nodes)} nodes`);
    }
    return pieces.join(' ').trim() || '—';
}
