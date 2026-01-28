import { describe, expect, it } from 'vitest';

import { normalizeSummaryEventPayload } from '../contractGuard';

describe('normalizeSummaryEventPayload', () => {
    it('normalizes liveView snapshot and enforces progress fields', () => {
        const payload = {
            liveView: {
                mode: 'spsa',
                progress: {
                    kind: 'updates',
                    completed: 10,
                    total: 20,
                },
            },
        };

        const normalized = normalizeSummaryEventPayload(payload) as {
            liveView?: { progress?: { completed?: number | null; total?: number | null; unitLabel?: string } };
        };
        expect(normalized.liveView?.progress?.completed).toBe(10);
        expect(normalized.liveView?.progress?.total).toBe(20);
        expect(normalized.liveView?.progress?.unitLabel).toBe('updates');
    });

    it('throws when progress lacks required fields', () => {
        const payload = {
            liveView: {
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
        expect(() => normalizeSummaryEventPayload({})).toThrow('summary.liveView is required');
    });

    it('rejects non-object payloads', () => {
        expect(() => normalizeSummaryEventPayload(null)).toThrow('summary event payload must be an object');
        expect(() => normalizeSummaryEventPayload('oops')).toThrow('summary event payload must be an object');
    });
});
