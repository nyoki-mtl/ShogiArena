import { parseTimeControlSpec } from './parse';
import type { ParsedTimeControlSpec } from '@/modules/live/types/time';

function pad2(value: number): string {
    return String(value).padStart(2, '0');
}

function formatClockSeconds(totalSeconds: number, options?: { padMinutes?: boolean }): string {
    const secondsSafe = Math.max(0, Math.trunc(totalSeconds));
    const minutesTotal = Math.floor(secondsSafe / 60);
    const seconds = secondsSafe % 60;
    const hours = Math.floor(minutesTotal / 60);
    const minutes = minutesTotal % 60;
    if (hours > 0) {
        return `${hours}:${pad2(minutes)}:${pad2(seconds)}`;
    }
    const padMinutes = options?.padMinutes === true;
    const minuteText = padMinutes ? pad2(minutesTotal) : `${minutesTotal}`;
    return `${minuteText}:${pad2(seconds)}`;
}

export function formatRemain(ms: number | string | null | undefined): string {
    const totalMs = Math.max(0, Number(ms ?? 0));
    const totalSeconds = Math.ceil(totalMs / 1000);
    return formatClockSeconds(totalSeconds);
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
    return formatClockSeconds(totalSeconds, { padMinutes: true });
}

export function formatByoyomi(ms: number | string | null | undefined): string {
    const totalMs = Math.max(0, Number(ms ?? 0));
    const seconds = Math.ceil(totalMs / 1000);
    return `${seconds}`;
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
