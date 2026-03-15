import type { KnipConfig } from 'knip';

const FRONTEND_ROOT = 'src/shogiarena/_core/interfaces/dashboard/frontend';
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
        `${FRONTEND_SRC}/**/*.test.{ts,tsx}`,
        `${FRONTEND_SRC}/**/workers/*.ts`,
      ],
      project: [
        `${FRONTEND_SRC}/**/*.{ts,tsx,js,jsx,mts,cts,cjs,mjs}`,
        `!${FRONTEND_SRC}/**/*.d.ts`,
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
