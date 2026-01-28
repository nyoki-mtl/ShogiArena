import { z } from 'zod';

// LiveEnvelope shared wrapper
export const liveEnvelopeSchema = <T extends z.ZodTypeAny>(payload: T) =>
    z.object({
        topic: z.string(),
        seq: z.number().int().nonnegative(),
        ts: z.number().int().nonnegative().optional(),
        payload,
    });

// summary.snapshot (full object, accept passthrough for forward compat)
export const summarySnapshotSchema = z.object({}).passthrough();

// games.delta (row-level operations)
const gamesDeltaRowSchema = z.object({
    op: z.enum(['add', 'update', 'remove']),
    row: z.record(z.string(), z.any()).optional(),
    id: z.string().optional(),
});

const gamesDeltaSchema = z.object({
    type: z.string().optional(), // bulk/delta for transitional compatibility
    rows: z.array(gamesDeltaRowSchema).optional(),
    snapshotMeta: z.record(z.string(), z.any()).optional(),
});

const gamesSnapshotSchema = z.object({
    type: z.literal('bulk').optional(),
    rows: z.array(z.record(z.string(), z.any())).optional(),
    snapshotMeta: z.record(z.string(), z.any()).optional(),
});

export const gamesPayloadSchema = z.union([gamesDeltaSchema, gamesSnapshotSchema]);
