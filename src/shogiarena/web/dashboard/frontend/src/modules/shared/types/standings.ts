// Shared shape for standings-like rows used by live/tournament tables.
// Keep this minimal to avoid forcing unrelated domains to align on optional fields.
export interface BaseStandingsRow {
    name: string;
    rating: number;
    games: number;
}
