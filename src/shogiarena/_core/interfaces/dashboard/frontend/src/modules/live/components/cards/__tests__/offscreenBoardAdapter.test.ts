import { describe, expect, it, vi } from 'vitest';
import { createOffscreenBoardAdapterCtor } from '@/modules/live/components/cards/worker/offscreen-board-adapter';
import type { LiveBoardAdapter } from '@/modules/live/types';

describe('offscreen board adapter', () => {
    it('resends an unchanged move history after the position is reset', () => {
        const setMoves = vi.fn();

        class FallbackAdapter implements LiveBoardAdapter {
            [key: string]: unknown;
            mount(): void {}
            setPositionFromSFEN(): void {}
            setMoves(moves: readonly string[]): void {
                setMoves([...moves]);
            }
        }

        const Adapter = createOffscreenBoardAdapterCtor(FallbackAdapter);
        const adapter = new Adapter();
        const moves = ['7g7f', '3c3d'];

        adapter.setMoves?.(moves);
        adapter.setPositionFromSFEN?.('startpos');
        adapter.setMoves?.(moves);

        expect(setMoves).toHaveBeenCalledTimes(2);
        expect(setMoves).toHaveBeenLastCalledWith(moves);
    });
});
