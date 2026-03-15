import { z, type ZodTypeAny, type ZodError } from 'zod';
import type {
    SpsaSummaryResponse,
    SpsaParamsResponse,
    SpsaUpdateDetailResponse,
    SpsaUpdateEntry,
} from '@/modules/spsa/types';

type ParsedPayload<T extends ZodTypeAny> = z.infer<T>;

export type { ZodError };

// ---------------------------------------------------------------------------
// Canonical schemas
// ---------------------------------------------------------------------------

const wdlSchema = z.object({
    wins: z.number(),
    losses: z.number(),
    draws: z.number(),
});

const spsaSummarySchema = z
    .object({
        wins: z.number(),
        losses: z.number(),
        draws: z.number(),
    })
    .passthrough();

const spsaParamsSchema = z
    .object({
        params: z.array(z.unknown()),
        num_params: z.number(),
        num_used: z.number(),
        num_clamped: z.number(),
        clamped_ratio: z.number().nullable(),
    })
    .passthrough();

const spsaUpdateEntrySchema = z
    .object({
        update_idx: z.number(),
    })
    .passthrough();

const spsaUpdateEntriesSchema = z.array(spsaUpdateEntrySchema);

const spsaUpdateDetailSchema = z
    .object({
        update_idx: z.number(),
        engines: z
            .object({
                baseline: z.string().nullable(),
                tuned: z.string().nullable(),
            })
            .passthrough(),
        wdl: wdlSchema,
        games: z.array(z.unknown()),
        games_count: z.number(),
    })
    .passthrough();

function parsePayload<T extends ZodTypeAny>(raw: unknown, schema: T): ParsedPayload<T> {
    const parsed = schema.safeParse(raw);
    if (!parsed.success) {
        throw parsed.error;
    }
    return parsed.data;
}

export function parseSpsaSummaryResponse(raw: unknown): SpsaSummaryResponse {
    return parsePayload(raw, spsaSummarySchema) as SpsaSummaryResponse;
}

export function parseSpsaParamsResponse(raw: unknown): SpsaParamsResponse {
    return parsePayload(raw, spsaParamsSchema) as SpsaParamsResponse;
}

export function parseSpsaUpdateEntries(raw: unknown): SpsaUpdateEntry[] {
    return parsePayload(raw, spsaUpdateEntriesSchema) as SpsaUpdateEntry[];
}

export function parseSpsaUpdateDetailResponse(raw: unknown): SpsaUpdateDetailResponse {
    return parsePayload(raw, spsaUpdateDetailSchema) as SpsaUpdateDetailResponse;
}
