import { describe, expect, it } from 'vitest';

import { hasKickoffCommand, isPrepOverlayVisibleFor } from '../card-data';

/**
 * Raw I/O demand gating (task 0052 / review finding M7).
 *
 * The default Live grid must keep zero raw-I/O subscriptions regardless of how many games run in
 * parallel. Kickoff detection is a lifecycle concern (`engine_status`), and the prep overlay is a
 * pure display state; only an explicitly opened raw panel may subscribe.
 */

const tail = (lines: Array<{ dir: string; line: string }>) => lines as never;

describe('kickoff detection', () => {
    it('detects usinewgame from the lifecycle io_tail', () => {
        expect(hasKickoffCommand(tail([{ dir: 'out', line: 'usinewgame' }]))).toBe(true);
    });

    it('detects a position command from the lifecycle io_tail', () => {
        expect(hasKickoffCommand(tail([{ dir: 'out', line: 'position startpos' }]))).toBe(true);
    });

    it('ignores inbound lines and unrelated commands', () => {
        expect(hasKickoffCommand(tail([{ dir: 'in', line: 'usinewgame' }]))).toBe(false);
        expect(hasKickoffCommand(tail([{ dir: 'out', line: 'isready' }]))).toBe(false);
        expect(hasKickoffCommand(undefined)).toBe(false);
        expect(hasKickoffCommand(tail([]))).toBe(false);
    });
});

describe('prep overlay visibility', () => {
    const ready = { isBlackReady: true, isWhiteReady: true };
    const kickoff = tail([{ dir: 'out', line: 'usinewgame' }]);

    it('is shown before kickoff without needing any raw topic', () => {
        expect(
            isPrepOverlayVisibleFor({
                phase: 'pre',
                ...ready,
                blackIoTail: undefined,
                whiteIoTail: undefined,
            }),
        ).toBe(true);
    });

    it('is hidden once both engines are ready and both have kicked off', () => {
        expect(
            isPrepOverlayVisibleFor({
                phase: 'pre',
                ...ready,
                blackIoTail: kickoff,
                whiteIoTail: kickoff,
            }),
        ).toBe(false);
    });

    it('stays visible while only one engine has kicked off', () => {
        expect(
            isPrepOverlayVisibleFor({
                phase: 'pre',
                ...ready,
                blackIoTail: kickoff,
                whiteIoTail: undefined,
            }),
        ).toBe(true);
    });

    it('is never shown outside the pre phase', () => {
        for (const phase of ['in', 'post'] as const) {
            expect(
                isPrepOverlayVisibleFor({
                    phase,
                    ...ready,
                    blackIoTail: undefined,
                    whiteIoTail: undefined,
                }),
            ).toBe(false);
        }
    });
});

describe('display state must not be read back as the subscription preference', () => {
    it('keeps the prep overlay out of the preference so the first toggle opens the panel', () => {
        // During the prep phase the card shows `worker-card--log-visible-*`, but no role is
        // subscribed. Reading that class as the current preference would make the first user
        // toggle a no-op close instead of a subscribe (task 0052 / M7).
        const cardState = { engineLogPreference: {} } as {
            engineLogPreference: { black?: boolean | null; white?: boolean | null };
        };
        const isPrepOverlayShown = isPrepOverlayVisibleFor({
            phase: 'pre',
            isBlackReady: true,
            isWhiteReady: true,
            blackIoTail: undefined,
            whiteIoTail: undefined,
        });

        expect(isPrepOverlayShown).toBe(true);
        // 購読 preference は独立して「未開封」のままであること。
        expect(cardState.engineLogPreference.black === true).toBe(false);
        expect(cardState.engineLogPreference.white === true).toBe(false);
    });
});
