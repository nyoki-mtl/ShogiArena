import { z, type ZodError } from 'zod';

export type { ZodError };

export type LiveMergeWorkerEnvelope = {
    topic: string;
    seq: number;
    ts?: number;
    payload: unknown;
};

const liveMergeWorkerEnvelopeSchema = z.object({
    topic: z.string(),
    seq: z.number().int().nonnegative().finite(),
    ts: z.number().nonnegative().finite().optional(),
    payload: z.unknown(),
});

export function parseLiveMergeWorkerEnvelope(raw: unknown): LiveMergeWorkerEnvelope {
    const parsed = liveMergeWorkerEnvelopeSchema.safeParse(raw);
    if (!parsed.success) {
        throw parsed.error;
    }
    const data = parsed.data;
    if (typeof data.topic !== 'string') {
        throw new Error('topic is required');
    }
    if (typeof data.seq !== 'number' || !Number.isFinite(data.seq)) {
        throw new Error('seq is required');
    }
    if (!Object.hasOwn(data, 'payload')) {
        throw new Error('payload is required');
    }
    return {
        topic: data.topic,
        seq: data.seq,
        ts: data.ts,
        payload: data.payload,
    };
}
