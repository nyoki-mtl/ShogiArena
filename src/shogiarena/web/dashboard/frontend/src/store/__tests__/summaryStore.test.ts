import { describe, it, expect, beforeEach } from 'vitest';
import { summaryStore, resetStore } from '../summaryStore';

describe('SummaryStore', () => {
    beforeEach(() => {
        resetStore();
    });

    describe('applyTournamentSummary', () => {
        it('stores tournament summary data', () => {
            const raw = {
                engines: ['EngineA', 'EngineB'],
                engineMeta: {
                    EngineA: {
                        name: 'EngineA',
                        engine_path: '/path/to/engineA',
                        merged_options: { Threads: 4 },
                    },
                    EngineB: {
                        name: 'EngineB',
                        engine_path: '/path/to/engineB',
                        merged_options: { Threads: 8 },
                    },
                },
                engineTimeControls: {
                    EngineA: 'byoyomi 10000',
                    EngineB: 'byoyomi 10000',
                },
                defaultTimeControl: 'byoyomi 10000',
            };

            const changed = summaryStore.applyTournamentSummary(raw);

            expect(changed).toBe(true);
            expect(summaryStore.getEngineNames()).toEqual(['EngineA', 'EngineB']);
            // activeSource is not auto-set; it's managed via setActiveSource() only
            // Data is still accessible via fallback when activeSource is null
            expect(summaryStore.getActiveSource()).toBe(null);
        });

        it('returns false when no engines change', () => {
            const raw = {
                engines: ['EngineA'],
                engineMeta: { EngineA: { name: 'EngineA' } },
            };

            summaryStore.applyTournamentSummary(raw);
            const changed = summaryStore.applyTournamentSummary(raw);

            expect(changed).toBe(false);
        });
    });

    describe('applySpsaSummary', () => {
        it('stores SPSA summary data', () => {
            const raw = {
                engines: ['Baseline', 'Tuned'],
                engineMeta: {
                    Baseline: { name: 'Baseline' },
                    Tuned: { name: 'Tuned' },
                },
                engineTimeControls: {
                    Baseline: 'depth 10',
                    Tuned: 'depth 10',
                },
            };

            const changed = summaryStore.applySpsaSummary(raw);

            expect(changed).toBe(true);
            expect(summaryStore.getEngineNames()).toEqual(['Baseline', 'Tuned']);
        });
    });

    describe('setActiveSource', () => {
        it('switches between tournament and spsa sources', () => {
            // Setup both sources
            summaryStore.applyTournamentSummary({
                engines: ['TournamentEngine'],
                engineMeta: { TournamentEngine: { name: 'TournamentEngine' } },
            });
            summaryStore.applySpsaSummary({
                engines: ['SpsaEngine'],
                engineMeta: { SpsaEngine: { name: 'SpsaEngine' } },
            });

            // Before setActiveSource, activeSource is null
            // Data is accessed via fallback (tournament first)
            expect(summaryStore.getActiveSource()).toBe(null);
            expect(summaryStore.getEngineNames()).toEqual(['TournamentEngine']);

            // Explicitly set to tournament
            summaryStore.setActiveSource('tournament');
            expect(summaryStore.getActiveSource()).toBe('tournament');
            expect(summaryStore.getEngineNames()).toEqual(['TournamentEngine']);

            // Switch to SPSA
            summaryStore.setActiveSource('spsa');
            expect(summaryStore.getActiveSource()).toBe('spsa');
            expect(summaryStore.getEngineNames()).toEqual(['SpsaEngine']);

            // Switch back to tournament
            summaryStore.setActiveSource('tournament');
            expect(summaryStore.getActiveSource()).toBe('tournament');
            expect(summaryStore.getEngineNames()).toEqual(['TournamentEngine']);
        });
    });

    describe('getEngineMeta', () => {
        it('returns merged engine meta with runtime options', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: {
                    EngineA: {
                        name: 'EngineA',
                        engine_path: '/path/to/engine',
                        merged_options: { Threads: 4, Hash: 256 },
                        resolved_options: { Threads: '4', Hash: '256' },
                        option_sources: { Threads: 'config', Hash: 'config' },
                    },
                },
            });

            // Apply runtime options
            summaryStore.applyRuntimeOptions('EngineA', {
                Threads: { current: 4, default: 1 },
                Hash: { current: 256, default: 16 },
            });

            const meta = summaryStore.getEngineMeta('EngineA');

            expect(meta).toBeDefined();
            expect(meta?.name).toBe('EngineA');
            expect(meta?.enginePath).toBe('/path/to/engine');
            expect(meta?.merged_options).toEqual({ Threads: 4, Hash: 256 });
            expect(meta?.runtime_usi_options).toEqual({
                Threads: { current: 4, default: 1 },
                Hash: { current: 256, default: 16 },
            });
        });

        it('returns undefined for unknown engine', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: { EngineA: { name: 'EngineA' } },
            });

            expect(summaryStore.getEngineMeta('UnknownEngine')).toBeUndefined();
        });
    });

    describe('applyRuntimeOptions', () => {
        it('merges runtime options with existing data', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: {
                    EngineA: {
                        name: 'EngineA',
                        runtime_usi_options: {
                            Threads: { current: 2 },
                        },
                    },
                },
            });

            summaryStore.applyRuntimeOptions('EngineA', {
                Threads: { default: 1 },
                Hash: { current: 128, default: 16 },
            });

            const meta = summaryStore.getEngineMeta('EngineA');
            expect(meta?.runtime_usi_options).toEqual({
                Threads: { current: 2, default: 1 },
                Hash: { current: 128, default: 16 },
            });
        });

        it('returns false when no changes', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: { EngineA: { name: 'EngineA' } },
            });

            summaryStore.applyRuntimeOptions('EngineA', { Threads: { current: 4 } });
            const changed = summaryStore.applyRuntimeOptions('EngineA', { Threads: { current: 4 } });

            expect(changed).toBe(false);
        });
    });

    describe('getTimeControl', () => {
        it('returns engine-specific time control', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA', 'EngineB'],
                engineMeta: {
                    EngineA: { name: 'EngineA' },
                    EngineB: { name: 'EngineB' },
                },
                engineTimeControls: {
                    EngineA: 'byoyomi 10000',
                    EngineB: 'depth 15',
                },
                defaultTimeControl: 'byoyomi 5000',
            });

            expect(summaryStore.getTimeControl('EngineA')).toBe('byoyomi 10000');
            expect(summaryStore.getTimeControl('EngineB')).toBe('depth 15');
        });

        it('falls back to default time control', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: { EngineA: { name: 'EngineA' } },
                defaultTimeControl: 'byoyomi 5000',
            });

            expect(summaryStore.getTimeControl('EngineA')).toBe('byoyomi 5000');
        });
    });

    describe('getInstance', () => {
        it('returns instance ID for engine', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA', 'EngineB'],
                engineMeta: {
                    EngineA: { name: 'EngineA' },
                    EngineB: { name: 'EngineB' },
                },
                engineInstances: {
                    EngineA: 'remote-1',
                    EngineB: null,
                },
            });

            expect(summaryStore.getInstance('EngineA')).toBe('remote-1');
            expect(summaryStore.getInstance('EngineB')).toBe(null);
        });
    });

    describe('hasValidOptionsData', () => {
        it('returns true when merged_options has data', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: {
                    EngineA: {
                        name: 'EngineA',
                        merged_options: { Threads: 4 },
                    },
                },
            });

            expect(summaryStore.hasValidOptionsData('EngineA')).toBe(true);
        });

        it('returns false when merged_options is empty', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: {
                    EngineA: {
                        name: 'EngineA',
                        merged_options: {},
                    },
                },
            });

            expect(summaryStore.hasValidOptionsData('EngineA')).toBe(false);
        });

        it('returns false for unknown engine', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: { EngineA: { name: 'EngineA' } },
            });

            expect(summaryStore.hasValidOptionsData('UnknownEngine')).toBe(false);
        });
    });

    describe('getViewModel', () => {
        it('returns a complete view model', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA', 'EngineB'],
                engineMeta: {
                    EngineA: {
                        name: 'EngineA',
                        engine_path: '/path/a',
                        merged_options: { Threads: 4 },
                    },
                    EngineB: {
                        name: 'EngineB',
                        engine_path: '/path/b',
                    },
                },
                engineTimeControls: {
                    EngineA: 'byoyomi 10000',
                },
                engineInstances: {
                    EngineA: 'remote-1',
                },
                defaultTimeControl: 'byoyomi 5000',
            });

            const viewModel = summaryStore.getViewModel();

            expect(viewModel.engines).toEqual(['EngineA', 'EngineB']);
            expect(viewModel.defaultTimeControl).toBe('byoyomi 5000');
            // activeSource is null (not auto-set), but actualSource shows where data came from
            expect(viewModel.activeSource).toBe(null);
            expect(viewModel.actualSource).toBe('tournament');

            const engineA = viewModel.engineMap.get('EngineA');
            expect(engineA).toBeDefined();
            expect(engineA?.name).toBe('EngineA');
            expect(engineA?.enginePath).toBe('/path/a');
            expect(engineA?.timeControl).toBe('byoyomi 10000');
            expect(engineA?.instanceId).toBe('remote-1');
            expect(engineA?.hasOptionsData).toBe(true);

            const engineB = viewModel.engineMap.get('EngineB');
            expect(engineB).toBeDefined();
            expect(engineB?.timeControl).toBe('byoyomi 5000'); // Falls back to default
            expect(engineB?.hasOptionsData).toBe(false);
        });
    });

    describe('subscribe', () => {
        it('notifies subscribers on changes', () => {
            const events: Array<{ type: string; source: string }> = [];

            const subscription = summaryStore.subscribe('engines', (event) => {
                events.push({ type: event.type, source: event.source });
            });

            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: { EngineA: { name: 'EngineA' } },
            });

            expect(events.length).toBe(1);
            expect(events[0]).toEqual({ type: 'engines', source: 'tournament' });

            subscription.unsubscribe();

            // Should not receive events after unsubscribe
            summaryStore.applySpsaSummary({
                engines: ['EngineB'],
                engineMeta: { EngineB: { name: 'EngineB' } },
            });

            expect(events.length).toBe(1);
        });

        it('all event type receives all events', () => {
            const events: Array<{ type: string }> = [];

            summaryStore.subscribe('all', (event) => {
                events.push({ type: event.type });
            });

            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                engineMeta: { EngineA: { name: 'EngineA' } },
            });

            summaryStore.applyRuntimeOptions('EngineA', { Threads: { current: 4 } });

            expect(events.length).toBe(2);
            expect(events[0].type).toBe('engines');
            expect(events[1].type).toBe('options');
        });
    });

    describe('data format handling', () => {
        it('handles enginesMeta array format', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA'],
                enginesMeta: [
                    {
                        name: 'EngineA',
                        engine_path: '/path/to/engine',
                        merged_options: { Threads: 4 },
                    },
                ],
            });

            const meta = summaryStore.getEngineMeta('EngineA');
            expect(meta?.merged_options).toEqual({ Threads: 4 });
        });

        it('merges both engineMeta and enginesMeta formats', () => {
            summaryStore.applyTournamentSummary({
                engines: ['EngineA', 'EngineB'],
                engineMeta: {
                    EngineA: {
                        name: 'EngineA',
                        merged_options: { Hash: 256 },
                    },
                },
                enginesMeta: [
                    {
                        name: 'EngineB',
                        merged_options: { Threads: 8 },
                    },
                ],
            });

            expect(summaryStore.getEngineMeta('EngineA')?.merged_options).toEqual({ Hash: 256 });
            expect(summaryStore.getEngineMeta('EngineB')?.merged_options).toEqual({ Threads: 8 });
        });
    });
});
