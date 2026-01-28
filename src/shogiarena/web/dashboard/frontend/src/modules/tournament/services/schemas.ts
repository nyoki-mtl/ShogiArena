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
        engineStats: z.record(z.string(), EngineStatsSchema).default({}),
        // Be defensive: malformed enginesMeta entries must not take down the dashboard.
        // We normalize and validate entries separately in the tournament normalizer.
        enginesMeta: z.array(z.unknown()).catch([]),
        engineTimeControls: z.record(z.string(), z.string()).optional(),
        defaultTimeControl: z.string().optional().nullable(),
        ratingInitial: numberWithDefault(1500),
        games: z
            .object({
                completed: numberWithDefault(0),
                total: numberWithDefault(0),
                cancelled: numberWithDefault(0),
            })
            .default({ completed: 0, total: 0, cancelled: 0 }),
        runDir: z.string().optional().nullable(),
        tournamentFinished: z
            .union([z.boolean(), z.literal('true'), z.literal('finished'), z.literal('completed')])
            .optional(),
        sprtConclusion: z.string().optional().nullable(),
        sprt: TournamentSprtSummarySchema.nullable().optional(),
        btd: TournamentBTDSummarySchema.nullable().optional(),
        tournamentType: z.string().optional().nullable(),
        pairResults: z.record(z.string(), z.any()).default({}),
        engineInstances: z.record(z.string(), z.string().nullable()).default({}),
    })
    .passthrough();
