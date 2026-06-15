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
            // 'hidden' still emits maps for error reporting but omits the sourceMappingURL comment,
            // so the production bundle does not advertise/expose source to casual viewers.
            sourcemap: command === 'build' ? 'hidden' : true,
            manifest: true,
            rollupOptions: {
                input: {
                    dashboard: path.resolve(__dirname, 'src/main.ts'),
                },
                output: {
                    // Split the large feature areas (and vendor code) into separate chunks so the
                    // single dashboard bundle no longer trips the 500 kB warning and the browser can
                    // download/cache them in parallel (each chunk is now well under 300 kB).
                    //
                    // Rollup still prints a benign "Circular chunk" advisory: `live` eagerly imports a
                    // constant from `spsa` and a normalizer from `tournament`, while those modules
                    // re-import live-side types — splitting the trio surfaces this at chunk granularity.
                    // There is NO real ES-module import cycle (`make ts-import-cycle-check` => 0), the
                    // emitted chunks are correctly ordered, and runtime is unaffected. Clearing the size
                    // warning (a real perf concern) is preferred over hiding the advisory by keeping one
                    // ~640 kB bundle. Tab-level dynamic import is tracked as a follow-up.
                    manualChunks(id: string) {
                        if (id.includes('node_modules')) {
                            return 'vendor';
                        }
                        if (id.includes('/modules/live/utils/liveNamespace/')) {
                            return 'diagnostics';
                        }
                        if (id.includes('/modules/spsa/')) {
                            return 'spsa';
                        }
                        if (id.includes('/modules/tournament/')) {
                            return 'tournament';
                        }
                        if (id.includes('/modules/live/')) {
                            return 'live';
                        }
                        return undefined;
                    },
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
