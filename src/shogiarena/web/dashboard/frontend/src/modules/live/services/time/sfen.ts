export function normalizeSFEN(sfen: string | null | undefined): string {
    const s = String(sfen ?? '').trim();
    if (s === '' || s === 'startpos') {
        return 'lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1';
    }
    if (s.startsWith('sfen ')) return s.slice(5);
    if (s.startsWith('position sfen ')) return s.slice(14);
    if (s.startsWith('position startpos')) return 'startpos';
    return s;
}

export function getStartingPlyNumber(sfen: string | null | undefined): number {
    const norm = normalizeSFEN(sfen);
    const parts = norm.split(' ');
    if (parts.length >= 4) {
        const n = Number.parseInt(parts[3], 10);
        if (!Number.isNaN(n) && n > 0) {
            return n;
        }
    }
    return 1;
}
