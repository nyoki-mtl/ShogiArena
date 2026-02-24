# Dashboard Typing Guidelines

最終更新: 2026-02

このドキュメントはダッシュボードの型設計の要点と運用ルールをまとめます。

## Global Declarations (`src/types`)

- **`.d.ts` ファイルは Window 拡張と公開 API の薄いラッパーのみを提供します**。実装詳細は含めません。
- 主な構成は次のとおりです。
  - `dashboard.d.ts`: `DashboardCore` と共有 API、Window 拡張。
  - `globals.d.ts`: タブ／ナビゲーション API。
  - `live.d.ts` ほか各モジュールの公開 API エントリ。
  - `types/shared.ts`: 共通ユーティリティ型（`JsonRequestOptions`, `RequestError` など）。

## Module-internal Types (`src/modules/<feature>/types`)

- **`types/public.ts` と `types/internal.ts` を基本構成**とし、必要に応じてサブドメイン別ファイル（例: `live/types/time.ts`）を追加します。
- **`types/index.ts` は副作用のない再エクスポートに限定**します。
- 代表的な構成：
  - `live`: `time.ts`、`updates.ts` に細分化。
  - `spsa`: WebSocket / API 応答型を包含。
  - `tournament`: スタンディングやマッチアップ用型を集約。
  - `instances`: REST レスポンス型が中心。
  - `match`: マッチ分析のサマリーと時系列型。
  - `sprt`: SPRT 判定結果と LLR 型。
  - `generate`: 生成サマリー型。

## Ongoing Tasks

1. **肥大化した public 型の整理** — `live` / `spsa` / `tournament` で snake_case と camelCase が混在しているため、正規化層での一本化を検討する。
2. **DashboardLive 名前空間の診断強化** — `LiveNamespaceDiagnostics` のプロバイダメタデータを UI へ露出する。
3. **共有レスポンス型の集約** — `Record<string, unknown>` ベースのレスポンスを `types/shared.ts` に抽出し、モジュール間の重複を減らす。

## Policies

- **Window 拡張なしで共有する型は `types/shared.ts` を起点に追加**し、用途が限定的ならモジュール内に閉じ込める。
- **API 互換性切り替え時は `public.ts` 側で `@deprecated` コメントを追加**し、削除計画を Issue 化して追跡する。
- **Legacy フィールドは正規化時に即例外を投げ**、対応テストを追加する（例: `loses` や snake_case フィールド）。
- **新しいモジュールを追加する場合は `public`/`internal` 構成を必須**とし、本ドキュメントに状況を記録する。

## 参照先

- モジュールの配置方針は [architecture.md](architecture.md) を参照。
- テストの型補助は [testing.md](testing.md) を参照。
