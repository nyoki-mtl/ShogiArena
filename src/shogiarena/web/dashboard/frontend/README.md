# Dashboard Frontend

Vite + Tailwind 製のダッシュボードです。主要コマンドは `package.json` の `frontend:*` スクリプトにまとまっています。

- 開発サーバ: `npm run frontend:dev`
- テスト: `npm run frontend:test`
- 型チェック: `npm run frontend:typecheck`
- 本番ビルド: `npm run frontend:build`
- ビジュアルテスト (Playwright): `npm run frontend:visual`

ビルド成果物は `src/shogiarena/web/dashboard/static/dist/` に出力されます（`.gitignore` 対象）。スタイルは Tailwind をベースにし、再利用可能なコンポーネントを `src/styles/components/*.css` の `@layer components` で定義しています。

## Documentation Map

- `docs/architecture.md` — モジュール構成、Window API ワイヤリング、リアルタイム更新方針のまとめ。
- `docs/typing.md` — グローバル／モジュール内部型のポリシーと棚卸しメモ。
- `docs/testing.md` — 特に Live 名前空間のテストヘルパ活用方法。

ドキュメントは設計方針を集約するため、構成変更や型の更新を行った際は上記ファイルを必ず更新してください。

## 開発フローのメモ

- API 呼び出しは `modules/shared/services/api.ts` の `requestJson` に統一します。`fetch` の直接利用は禁止です。
- 致命的なエラーは `crash()` 経由で `DashboardFatalError` を投げ、UI の不整合を防ぎます。
- Live モジュールのテストは `setupLiveNamespace` / `teardownLiveNamespace` を利用し、Window 汚染を残さないようにしてください（詳細は `docs/testing.md`）。

## リファレンス

- Tailwind 設定: `tailwind.config.cjs`
- Vite 設定: `vite.config.ts`
- 型チェック用 TS 設定: `tsconfig.typecheck.json`
