# Dashboard Typing

このドキュメントはダッシュボードの型設計の要点と運用ルールをまとめます。

## 基本方針

- 公開 API は `src/shogiarena/web/dashboard/frontend/src/modules/*/types` に集約する。
- 内部限定の型は `types/internal.ts` などの専用ファイルに閉じ込める。
- `window` 拡張は `src/shogiarena/web/dashboard/frontend/src/types/*.d.ts` に集約する。

## 参照先

- モジュールの配置方針は `architecture.md` を参照。
- テストの型補助は `testing.md` を参照。
