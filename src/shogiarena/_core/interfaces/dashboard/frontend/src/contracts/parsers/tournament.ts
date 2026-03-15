import type { z, ZodError, ZodTypeAny } from 'zod';
import type { TournamentSummary } from '@/modules/tournament/types';
import { TournamentSummarySchema } from '@/modules/tournament/services/schemas';

type ParsedPayload<T extends ZodTypeAny> = z.infer<T>;

export type { ZodError };

function parsePayload<T extends ZodTypeAny>(raw: unknown, schema: T): ParsedPayload<T> {
    const parsed = schema.safeParse(raw);
    if (!parsed.success) {
        throw parsed.error;
    }
    return parsed.data;
}

export function parseTournamentSummary(raw: unknown): TournamentSummary {
    return parsePayload(raw, TournamentSummarySchema) as TournamentSummary;
}
