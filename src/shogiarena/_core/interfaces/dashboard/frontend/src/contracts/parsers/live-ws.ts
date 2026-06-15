import type { z, ZodError, ZodTypeAny } from 'zod';
import { liveEnvelopeSchema, gamesPayloadSchema, summarySnapshotSchema } from '@/modules/live/services/updates/schema';
import type { LiveEnvelope as BaseLiveEnvelope } from '@/modules/live/services/updates/ws-types';
import type { LiveWsGamesPayload, LiveWsSummaryPayload } from '../generated/live-ws';

export type { ZodError };

type LiveEnvelopeWithPayload<T> = BaseLiveEnvelope<T> & {
    payload: T;
};

function parseLiveEnvelope<T extends ZodTypeAny>(raw: unknown, payloadSchema: T): LiveEnvelopeWithPayload<z.infer<T>> {
    const parsed = liveEnvelopeSchema(payloadSchema).safeParse(raw);
    if (!parsed.success) {
        throw parsed.error;
    }
    return parsed.data as LiveEnvelopeWithPayload<z.infer<T>>;
}

export function parseLiveSummaryEnvelope(raw: unknown): LiveEnvelopeWithPayload<LiveWsSummaryPayload> {
    const parsed = parseLiveEnvelope(raw, summarySnapshotSchema);
    return parsed as LiveEnvelopeWithPayload<LiveWsSummaryPayload>;
}

export function parseLiveGamesEnvelope(raw: unknown): LiveEnvelopeWithPayload<LiveWsGamesPayload> {
    const parsed = parseLiveEnvelope(raw, gamesPayloadSchema);
    return parsed as LiveEnvelopeWithPayload<LiveWsGamesPayload>;
}

export function parseSummaryPayload(raw: unknown): LiveEnvelopeWithPayload<LiveWsSummaryPayload> {
    return parseLiveSummaryEnvelope(raw);
}

export function parseGamesPayload(raw: unknown): LiveEnvelopeWithPayload<LiveWsGamesPayload> {
    return parseLiveGamesEnvelope(raw);
}
