import type { WorkerSnapshot } from '@/types/live';
import { gameResultLabel } from '@/modules/shared/utils/game-result';

export const DEFAULT_INITIAL_SFEN = 'startpos';
export const INCREMENT_PLACEHOLDER = '\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0';

type ResultContext = {
    game_result?: unknown;
};

export function createEmptyWorkerSnapshot(): WorkerSnapshot {
    return {
        game_id: null,
        initial_sfen: DEFAULT_INITIAL_SFEN,
        sfen: null,
        black_name: null,
        white_name: null,
        moves: [],
        ki2_moves: [],
        eval_black: [],
        eval_white: [],
        nodes_values: [],
        depth_values: [],
        seldepth_values: [],
        move_times_ms: [],
        wall_times_ms: [],
        latency_deltas_ms: [],
        latency_alerts: [],
        current_ply: 0,
    };
}

export function hasTerminalResult(snapshot: Pick<WorkerSnapshot, 'game_result'> | null | undefined): boolean {
    return typeof snapshot?.game_result === 'string' && snapshot.game_result.trim().length > 0;
}

export function resultCodeToKifJP(context?: ResultContext | null): string {
    const resultLabel = gameResultLabel(context?.game_result);
    return resultLabel ?? '結果不明';
}
