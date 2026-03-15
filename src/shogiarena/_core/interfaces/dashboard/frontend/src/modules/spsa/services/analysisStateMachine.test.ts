import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createAnalysisStateMachine, type AnalysisStateMachineCallbacks } from './analysisStateMachine';

describe('AnalysisStateMachine', () => {
    let callbacks: AnalysisStateMachineCallbacks;

    beforeEach(() => {
        vi.useFakeTimers();
        callbacks = {
            onStateChange: vi.fn(),
            onAutoRetry: vi.fn(),
        };
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('starts in idle state', () => {
        const sm = createAnalysisStateMachine(callbacks);
        expect(sm.getState()).toBe('idle');
    });

    it('transitions to loading on startLoading', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        expect(sm.getState()).toBe('loading');
        expect(callbacks.onStateChange).toHaveBeenCalledWith(expect.objectContaining({ from: 'idle', to: 'loading' }));
    });

    it('transitions to ready on handleResponse("ready")', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.handleResponse('ready');
        expect(sm.getState()).toBe('ready');
    });

    it('transitions to warming on handleResponse("warming")', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.handleResponse('warming');
        expect(sm.getState()).toBe('warming');
    });

    it('transitions to error on handleResponse("error")', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.handleResponse('error');
        expect(sm.getState()).toBe('error');
    });

    it('auto-transitions to warming after LOADING_TO_WARMING_MS', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        vi.advanceTimersByTime(5_000);
        expect(sm.getState()).toBe('warming');
        expect(callbacks.onStateChange).toHaveBeenCalledWith(
            expect.objectContaining({ from: 'loading', to: 'warming', reason: 'timeout' }),
        );
    });

    it('auto-transitions to error after WARMING_TO_ERROR_MS', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        vi.advanceTimersByTime(30_000);
        expect(sm.getState()).toBe('error');
    });

    it('schedules auto-retry after error', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.handleResponse('error');
        expect(callbacks.onAutoRetry).not.toHaveBeenCalled();
        vi.advanceTimersByTime(10_000);
        expect(callbacks.onAutoRetry).toHaveBeenCalledTimes(1);
    });

    it('schedules auto-retry after timeout error', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        vi.advanceTimersByTime(30_000); // triggers error via timeout
        expect(sm.getState()).toBe('error');
        vi.advanceTimersByTime(10_000);
        expect(callbacks.onAutoRetry).toHaveBeenCalledTimes(1);
    });

    it('reset returns to idle and clears timers', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.reset();
        expect(sm.getState()).toBe('idle');
        // Timers should not fire after reset
        vi.advanceTimersByTime(50_000);
        // Only transitions: idle->loading, loading->idle
        const changeCalls = (callbacks.onStateChange as ReturnType<typeof vi.fn>).mock.calls;
        expect(changeCalls).toHaveLength(2);
    });

    it('destroy clears all timers', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.destroy();
        vi.advanceTimersByTime(50_000);
        // Only transition: idle->loading (no warming/error from timers)
        const changeCalls = (callbacks.onStateChange as ReturnType<typeof vi.fn>).mock.calls;
        expect(changeCalls).toHaveLength(1);
    });

    it('server-driven warming sets error timer for remaining duration', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.handleResponse('warming');
        expect(sm.getState()).toBe('warming');
        // Should transition to error after WARMING_TO_ERROR_MS - LOADING_TO_WARMING_MS = 25s
        vi.advanceTimersByTime(25_000);
        expect(sm.getState()).toBe('error');
    });

    it('does not duplicate transitions for same state', () => {
        const sm = createAnalysisStateMachine(callbacks);
        sm.startLoading();
        sm.handleResponse('ready');
        sm.handleResponse('ready'); // same state, no transition
        const changeCalls = (callbacks.onStateChange as ReturnType<typeof vi.fn>).mock.calls;
        // idle->loading, loading->ready
        expect(changeCalls).toHaveLength(2);
    });
});
