import { defineConfig } from 'vite';
import path from 'node:path';

export default defineConfig(({ command }) => {
    return {
        root: __dirname,
        plugins: [],
        // The dashboard HTML is served from the run directory (`/index.html`),
        // while Vite outputs bundles under `/static/dist`. Use an absolute base for build
        // so that dynamic imports and worker chunks resolve correctly.
        base: command === 'build' ? '/static/dist/' : '/',
        build: {
            outDir: path.resolve(__dirname, '../static/dist'),
            emptyOutDir: true,
            sourcemap: true,
            manifest: true,
            rollupOptions: {
                input: {
                    dashboard: path.resolve(__dirname, 'src/main.ts'),
                },
            },
        },
        server: {
            port: 5173,
            open: false,
            fs: {
                allow: [path.resolve(__dirname, '../static')],
            },
        },
        preview: {
            port: 4173,
        },
        resolve: {
            alias: {
                '@': path.resolve(__dirname, 'src'),
                '@modules': path.resolve(__dirname, 'src/modules'),
                '@styles': path.resolve(__dirname, 'src/styles'),
                '@types': path.resolve(__dirname, 'src/types'),
                '@static': path.resolve(__dirname, '../static'),
            },
        },
    };
});
