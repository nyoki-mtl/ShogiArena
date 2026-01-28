import { defineConfig } from 'vitest/config';
import path from 'node:path';

export default defineConfig({
    root: __dirname,
    resolve: {
        alias: {
            '@': path.resolve(__dirname, 'src'),
            '@modules': path.resolve(__dirname, 'src/modules'),
            '@styles': path.resolve(__dirname, 'src/styles'),
            '@types': path.resolve(__dirname, 'src/types'),
            '@static': path.resolve(__dirname, '../static'),
        },
    },
    test: {
        environment: 'jsdom',
        globals: true,
        setupFiles: [path.join(__dirname, 'src/test/setup.ts')],
        include: [path.join(__dirname, 'src/**/*.{test,spec}.{ts,tsx,js,jsx}')],
        exclude: ['**/node_modules/**', '**/dist/**'],
        testTimeout: 15_000,
        hookTimeout: 15_000,
    },
});
