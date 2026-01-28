export function computeSfenBasePlies(sfen: string | null | undefined): number {
    if (typeof sfen !== 'string' || !sfen.length || sfen === 'startpos') {
        return 0;
    }

    const tokens = sfen.startsWith('sfen ') ? sfen.slice(5).split(/\s+/) : sfen.split(/\s+/);
    if (tokens.length < 4) {
        return 0;
    }

    const moveNumberRaw = Number.parseInt(tokens[3], 10);
    if (!Number.isFinite(moveNumberRaw) || moveNumberRaw <= 1) {
        return 0;
    }

    return Math.max(0, moveNumberRaw - 1);
}
