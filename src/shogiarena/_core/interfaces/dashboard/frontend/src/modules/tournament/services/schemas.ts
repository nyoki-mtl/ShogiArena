import { z } from 'zod';

const EngineStatsSchema = z
    .object({
        wins: z.number().int().nonnegative().optional(),
        draws: z.number().int().nonnegative().optional(),
        losses: z.number().int().nonnegative().optional(),
        games: z.number().int().nonnegative().optional(),
        rating: z.number().finite().nullable().optional(),
        score: z.number().finite().nullable().optional(),
    })
    .strict();

const numberWithDefault = (fallback: number) =>
    z.preprocess((val) => (val === null ? undefined : val), z.number().finite().optional().default(fallback));

export const TournamentBTDSummarySchema = z
    .object({
        ratings: z
            .record(
                z.string(),
                z.object({
                    elo: z.number().finite().optional(),
                    se: z.number().finite().optional(),
                }),
            )
            .optional(),
        anchor: z.preprocess((val) => (val === null ? undefined : val), z.string().trim().min(1).optional()),
        rating_cov: z.record(z.string(), z.record(z.string(), z.number().finite())).optional(),
    })
    .passthrough();

export const TournamentSprtSummarySchema = z
    .object({
        llr: z.number().finite().optional(),
        lower: z.number().finite().optional(),
        upper: z.number().finite().optional(),
        decision: z.string().optional(),
        games: z.number().int().nonnegative().optional(),
    })
    .passthrough();

export const TournamentSummarySchema = z
    .object({
        engines: z.array(z.string().min(1)).default([]),
        engine_stats: z.record(z.string(), EngineStatsSchema).default({}),
        // Be defensive: malformed engines_meta entries must not take down the dashboard.
        // We normalize and validate entries separately in the tournament normalizer.
        engines_meta: z.array(z.unknown()).catch([]),
        engine_time_controls: z.record(z.string(), z.string()).optional(),
        default_time_control: z.string().optional().nullable(),
        rating_initial: numberWithDefault(1500),
        games: z
            .object({
                completed: numberWithDefault(0),
                total: numberWithDefault(0),
                cancelled: numberWithDefault(0),
            })
            .default({ completed: 0, total: 0, cancelled: 0 }),
        run_dir: z.string().optional().nullable(),
        tournament_finished: z.boolean().optional(),
        sprt_conclusion: z.string().optional().nullable(),
        sprt: TournamentSprtSummarySchema.nullable().optional(),
        btd: TournamentBTDSummarySchema.nullable().optional(),
        tournament_type: z.string().optional().nullable(),
        pair_results: z.record(z.string(), z.any()).default({}),
        engine_instances: z.record(z.string(), z.string().nullable()).default({}),
    })
    .passthrough();
