/**
 * The catalogue of dashboard runtime modes.
 *
 * A leaf module on purpose: it imports nothing, so every other module — types,
 * services, normalizers — can depend on it without a cycle.
 *
 * The type is *derived* from the array rather than written alongside it. That
 * single fact is what turns "add a mode" from a search-and-hope exercise into a
 * compiler-checked one: every `Record<DashboardRuntimeMode, …>` in the codebase
 * fails to compile until the new mode is answered for.
 *
 * The motivating history: the `csa` mode was added without this, and four
 * separate hand-written branches silently degraded it — a mode that fell through
 * an allow-list became `unknown`, and a page ended up wearing another profile's
 * tabs. None of it produced an error; it produced wrong behaviour.
 */
export const DASHBOARD_RUNTIME_MODES = ['tournament', 'spsa', 'match', 'sprt', 'generate', 'csa', 'unknown'] as const;

export type DashboardRuntimeMode = (typeof DASHBOARD_RUNTIME_MODES)[number];

/**
 * Is this string one of the modes we know?
 *
 * The `unknown` fallback for genuinely unrecognised input stays correct — that
 * is what it is for. What this removes is the other path: a *valid* mode falling
 * through a hand-maintained list and being treated as unrecognised.
 */
export function isDashboardRuntimeMode(value: unknown): value is DashboardRuntimeMode {
    return typeof value === 'string' && (DASHBOARD_RUNTIME_MODES as readonly string[]).includes(value);
}
