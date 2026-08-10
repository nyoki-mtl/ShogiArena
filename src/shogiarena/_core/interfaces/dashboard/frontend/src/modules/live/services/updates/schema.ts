import type { JsonValue } from '@/types/shared';
import type { LiveViewMode, LiveViewProgressKind, LiveViewSnapshot } from '../../types/public';
import { z } from 'zod';

const nonNegativeIntSchema = z.number().int().nonnegative().finite();

/**
 * The live-view modes this parser accepts, spelled as a `Record` so a new
 * dashboard mode is a compile error here rather than a rejected message.
 *
 * A hand-written enum drifted from `LiveViewMode` and never learned `csa`. The
 * cost was silent and total: every summary envelope on a CSA page failed
 * validation, so the run roster froze at whatever the initial REST fetch
 * returned and nothing on the page updated again. Failing validation is not a
 * degraded mode — it is the message never arriving.
 */
const LIVE_VIEW_MODE_KEYS: Record<LiveViewMode, true> = {
    tournament: true,
    spsa: true,
    match: true,
    sprt: true,
    csa: true,
    unknown: true,
};

const LIVE_VIEW_PROGRESS_KIND_KEYS: Record<LiveViewProgressKind, true> = {
    games: true,
    updates: true,
    match: true,
    sprt: true,
    unknown: true,
};

function enumKeys<T extends string>(flags: Record<T, true>): [T, ...T[]] {
    return Object.keys(flags) as [T, ...T[]];
}

const jsonObjectValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
    z.union([z.string(), z.number().finite(), z.boolean(), z.null(), z.array(jsonValueSchema), jsonObjectSchema]),
);

const jsonObjectSchema = z.record(z.lazy(() => jsonValueSchema));

const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() => jsonObjectValueSchema);

const liveViewProgressSchema = z
    .object({
        kind: z.enum(enumKeys(LIVE_VIEW_PROGRESS_KIND_KEYS)).optional(),
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
        mode: z.enum(enumKeys(LIVE_VIEW_MODE_KEYS)),
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
