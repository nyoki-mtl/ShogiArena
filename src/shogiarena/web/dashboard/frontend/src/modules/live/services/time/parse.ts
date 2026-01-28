import type { ParsedTimeControlSpec } from '@/modules/live/types/time';

export function parseTimeControlSpec(spec: string | null | undefined): ParsedTimeControlSpec {
    const s = String(spec ?? '').trim();
    const ret: ParsedTimeControlSpec = {
        mode: 'time',
        initial: 0,
        byoyomi: 0,
        increment: 0,
        fixedMs: 0,
        depth: null,
        nodes: null,
        marginMs: null,
        allowTimeout: false,
        maxWaitMs: null,
    };
    if (!s) return ret;
    const [base, ...suffix] = s.split(';');
    if (base.startsWith('fx')) {
        ret.mode = 'fixed';
        ret.fixedMs = Number.parseInt(base.slice(2), 10) || 0;
    } else if (base === 's') {
        ret.mode = 'search';
    } else if (base.startsWith('t')) {
        ret.mode = 'time';
        let body = base.slice(1);
        let tail = '';
        if (body.includes('+')) {
            const parts = body.split('+');
            body = parts[0];
            tail = parts[1] || '';
        }
        const timeMs = Number.parseInt(body || '0', 10) || 0;
        ret.initial = Math.floor(timeMs / 1000);
        if (tail.startsWith('b')) {
            const byoMs = Number.parseInt(tail.slice(1) || '0', 10) || 0;
            ret.byoyomi = Math.floor(byoMs / 1000);
        } else if (tail.startsWith('i')) {
            const incMs = Number.parseInt(tail.slice(1) || '0', 10) || 0;
            ret.increment = Math.floor(incMs / 1000);
        }
    }
    for (const p of suffix) {
        if (!p) continue;
        if (p.startsWith('d')) ret.depth = Number.parseInt(p.slice(1), 10) || 0;
        else if (p.startsWith('n')) ret.nodes = Number.parseInt(p.slice(1), 10) || 0;
        else if (p.startsWith('m')) ret.marginMs = Number.parseInt(p.slice(1), 10) || 0;
        else if (p === 'at') ret.allowTimeout = true;
        else if (p.startsWith('wt')) ret.maxWaitMs = Number.parseInt(p.slice(2), 10) || 0;
    }
    return ret;
}
