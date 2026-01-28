import type { KnipConfig } from 'knip';

const FRONTEND_ROOT = 'src/shogiarena/web/dashboard/frontend';
const FRONTEND_SRC = `${FRONTEND_ROOT}/src`;

const config: KnipConfig = {
  ignoreExportsUsedInFile: {
    interface: true,
    type: true,
  },
  workspaces: {
    '.': {
      entry: [
        `${FRONTEND_SRC}/main.ts`,
        `${FRONTEND_SRC}/bootstrap.ts`,
      ],
      project: [
        `${FRONTEND_SRC}/**/*.{ts,tsx,js,jsx,mts,cts,cjs,mjs}`,
        `!${FRONTEND_SRC}/**/*.d.ts`,
        `${FRONTEND_ROOT}/vite.config.ts`,
        `${FRONTEND_ROOT}/vitest.config.ts`,
        `${FRONTEND_ROOT}/tailwind.config.cjs`,
        `${FRONTEND_ROOT}/postcss.config.cjs`,
      ],
      ignore: [
        `${FRONTEND_ROOT}/docs/**`,
        `${FRONTEND_ROOT}/public/**`,
      ],
      typescript: {
        config: [
          `${FRONTEND_ROOT}/tsconfig.json`,
          `${FRONTEND_ROOT}/tsconfig.typecheck.json`,
          `${FRONTEND_ROOT}/tsconfig.node.json`,
        ],
      },
      vite: {
        config: [`${FRONTEND_ROOT}/vite.config.ts`],
      },
      vitest: {
        config: [`${FRONTEND_ROOT}/vitest.config.ts`],
      },
      tailwind: {
        config: [`${FRONTEND_ROOT}/tailwind.config.cjs`],
      },
      postcss: {
        config: [`${FRONTEND_ROOT}/postcss.config.cjs`],
      },
    },
  },
};

export default config;
