import { describe, expect, it } from 'vitest';

import { parseTimeControlSpec } from '../../shared/utils/time-control';
import { formatTimeControl } from './helpers';

describe('formatTimeControl', () => {
    it('formats a standard incremental time control (mode "time")', () => {
        // Regression: the formatter previously only handled mode === "incremental",
        // which the parser never emits, so standard specs fell through to the raw string.
        const result = formatTimeControl('t300000+i10000', parseTimeControlSpec);
        expect(result).toContain('Initial');
        expect(result).toContain('Inc');
        expect(result).not.toBe('t300000+i10000');
    });

    it('formats byoyomi time controls', () => {
        const result = formatTimeControl('t300000+b10000', parseTimeControlSpec);
        expect(result).toContain('Initial');
        expect(result).toContain('Byo');
    });

    it('omits zero increment/byoyomi components', () => {
        const result = formatTimeControl('t300000', parseTimeControlSpec);
        expect(result).toContain('Initial');
        expect(result).not.toContain('Inc');
        expect(result).not.toContain('Byo');
    });

    it('formats fixed time controls', () => {
        const result = formatTimeControl('fx5000', parseTimeControlSpec);
        expect(result).toContain('Fixed');
    });
});
