import { describe, expect, it } from 'vitest';

import { normalizeSummaryEventPayload } from '../contract-guard';

describe('normalizeSummaryEventPayload', () => {
    it('normalizes liveView snapshot and enforces progress fields', () => {
        const payload = {
            live_view: {
                mode: 'spsa',
                progress: {
                    kind: 'updates',
                    completed: 10,
                    total: 20,
                },
            },
        };

        const normalized = normalizeSummaryEventPayload(payload) as {
            live_view?: { progress?: { completed?: number | null; total?: number | null; unit_label?: string } };
        };
        expect(normalized.live_view?.progress?.completed).toBe(10);
        expect(normalized.live_view?.progress?.total).toBe(20);
        expect(normalized.live_view?.progress?.unit_label).toBe('updates');
    });

    it('throws when progress lacks required fields', () => {
        const payload = {
            live_view: {
                mode: 'spsa',
                progress: {
                    kind: 'updates',
                    completed: null,
                    total: null,
                },
            },
        };

        expect(() => normalizeSummaryEventPayload(payload)).toThrow(/progress requires completed and total/);
    });

    it('throws when liveView is missing', () => {
        expect(() => normalizeSummaryEventPayload({})).toThrow('summary.live_view is required');
    });

    it('rejects non-object payloads', () => {
        expect(() => normalizeSummaryEventPayload(null)).toThrow('summary event payload must be an object');
        expect(() => normalizeSummaryEventPayload('oops')).toThrow('summary event payload must be an object');
    });
});
