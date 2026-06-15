import type { JsonValue } from '@/types/shared';
import type { LiveViewSnapshot } from '../../types/public';
import { z } from 'zod';

const nonNegativeIntSchema = z.number().int().nonnegative().finite();

const jsonObjectValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
    z.union([z.string(), z.number().finite(), z.boolean(), z.null(), z.array(jsonValueSchema), jsonObjectSchema]),
);

const jsonObjectSchema = z.record(z.lazy(() => jsonValueSchema));

const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() => jsonObjectValueSchema);

const liveViewProgressSchema = z
    .object({
        kind: z.enum(['games', 'updates', 'match', 'sprt', 'unknown']).optional(),
        unit_label: z.string().optional(),
        completed: z.number().int().nonnegative().nullable().optional(),
        total: z.number().int().nonnegative().nullable().optional(),
        cancelled: z.number().int().nullable().optional(),
        is_final: z.boolean().optional(),
        state: z.enum(['normal', 'paused', 'draining', 'finished']).optional(),
        updated_at: z.string().optional(),
    })
    .catchall(jsonValueSchema)
    .passthrough();

const liveViewSnapshotSchema = z
    .object({
        version: z.number().nullable(),
        mode: z.enum(['tournament', 'spsa', 'match', 'sprt', 'unknown']),
        progress: liveViewProgressSchema.nullable().optional(),
    })
    .catchall(jsonValueSchema)
    .passthrough()
    .transform((value) => value as LiveViewSnapshot);

// LiveEnvelope shared wrapper
export const liveEnvelopeSchema = <T extends z.ZodTypeAny>(payload: T) =>
    z.object({
        topic: z.string().min(1),
        seq: nonNegativeIntSchema,
        ts: nonNegativeIntSchema.optional(),
        payload,
    });

const summaryGamesSchema = z
    .object({
        completed: nonNegativeIntSchema,
        total: nonNegativeIntSchema,
        cancelled: nonNegativeIntSchema.optional(),
    })
    .catchall(jsonValueSchema);

// summary.snapshot
export const summarySnapshotSchema = z
    .object({
        games: summaryGamesSchema,
        live_view: liveViewSnapshotSchema.nullish(),
        is_summary_ready: z.boolean().optional(),
        timestamp: z.string().optional(),
    })
    .catchall(jsonValueSchema);

// games.delta (row-level operations)
const gamesDeltaRowSchema = z.union([
    z.object({
        op: z.literal('remove'),
        id: z.string().min(1),
    }),
    z.object({
        op: z.enum(['add', 'update']),
        row: jsonObjectSchema,
    }),
]);

const gamesDeltaSchema = z.object({
    kind: z.literal('delta'),
    revision: nonNegativeIntSchema,
    base_revision: nonNegativeIntSchema,
    rows: z.array(gamesDeltaRowSchema),
    snapshot_meta: jsonObjectSchema,
});

const gamesSnapshotSchema = z.object({
    kind: z.literal('bulk'),
    revision: nonNegativeIntSchema,
    base_revision: nonNegativeIntSchema.nullable(),
    rows: z.array(jsonObjectSchema),
    snapshot_meta: jsonObjectSchema,
});

export const gamesPayloadSchema = z.union([gamesDeltaSchema, gamesSnapshotSchema]);
