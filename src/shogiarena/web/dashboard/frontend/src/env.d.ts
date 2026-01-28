/// <reference types="vite/client" />

import type { Vitest } from 'vitest';

declare global {
    interface ImportMeta {
        readonly vitest?: Vitest;
    }

    interface Math {
        erf?(x: number): number;
    }
}
