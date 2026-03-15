# Frontend Visual Testing Plan

This repository still lacks automated screenshot-based regression checks. The long-term goal is to pair component-level unit tests with lightweight visual diffs so that layout regressions on the dashboard are caught immediately.

## Suggested Tooling

- [Playwright](https://playwright.dev/) for deterministic browser automation and screenshot capture.
- [Percy](https://percy.io/) (or an equivalent diffing service such as Chromatic) for snapshot storage and review.

## Proposed Workflow

1. **Install tooling**
   ```bash
   npm install --save-dev @playwright/test
   npx playwright install
   ```
2. **Author scenario-driven specs** under `visual-tests/` that
   - load the rendered dashboard HTML produced by `write_dashboard_assets` (e.g., `<run_dir>/index.html`) via `page.goto` (either through `file://` or a static server),
   - wait for the relevant dashboard module to hydrate, and
   - capture `page.screenshot({ fullPage: true })` or element-specific snapshots.
   Run `shogiarena dashboard serve --run-dir <path>` (or invoke `write_dashboard_assets` directly) before the tests so that the HTML reflects the latest bundler output.
3. **Publish screenshots** to Percy (`percy exec -- playwright test`) in CI. Percy handles diffing against the previous baseline.
4. **Gate merges** on Percy status and visual review.

ローカルチェックは `npm run frontend:visual` を想定しています（`playwright.config.ts` でビルド済みダッシュボードを読み込むよう調整してください）。Percy を利用する場合は以下のように実行できます。

```bash
PERCY_TOKEN=... npx percy exec -- npm run frontend:visual
```

For now, component-level HTML snapshot tests (see frontend の `src/modules/tournament/__tests__/`) provide a textual regression signal. Moving those HTML strings into Playwright-driven screenshots is the next step once the tooling above is in place.
