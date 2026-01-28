import { normalizeLiveGameRecord } from '@/modules/live/services/updates/normalizers';
import type { LiveGameRecord } from '@/modules/live/types/public';
import type { WorkerSnapshotRecord } from '@/modules/live/types/updates';
import type { MutableJsonObject } from '@/types/shared';

function ensureObject(value: unknown, context: string): MutableJsonObject {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`${context}: expected object`);
    }
    return value as MutableJsonObject;
}

export function normalizeGamesListResponse(raw: unknown, context = 'games_list'): WorkerSnapshotRecord[] {
    const payload = ensureObject(raw, context);
    const gamesRaw = payload.games;
    if (!Array.isArray(gamesRaw)) {
        throw new Error(`${context}.games: expected array`);
    }

    return gamesRaw.map((entry, index) => {
        if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
            throw new Error(`${context}.games[${index}]: expected object`);
        }
        return normalizeLiveGameRecord(entry as LiveGameRecord);
    });
}
