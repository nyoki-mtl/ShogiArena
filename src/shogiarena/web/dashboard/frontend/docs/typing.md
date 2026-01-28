# Typing Guidelines (2025-10-07)

## Global Declarations (`src/types`)

- `.d.ts` ファイルは Window 拡張と公開 API の薄いラッパーのみを提供します。実装詳細は含めません。
- 2025-10-07 時点の行数は 317 行で、主な構成は次のとおりです。
  - `dashboard.d.ts`: `DashboardCore` と共有 API、Window 拡張。
  - `globals.d.ts`: タブ／ナビゲーション API。
  - `live.d.ts` ほか各モジュールの公開 API エントリ。
  - `types/shared.ts`: 共通ユーティリティ型（`JsonRequestOptions`, `RequestError` など）。

## Module-internal Types (`src/modules/<feature>/types`)

- `types/public.ts` と `types/internal.ts` を基本構成とし、必要に応じてサブドメイン別ファイル（例: `live/types/time.ts`）を追加します。
- `types/index.ts` は副作用のない再エクスポートに限定します。
- 代表的なボリューム（2025-10-07 時点）：
  - `live`: public 285 行 / internal 62 行。`time.ts`、`updates.ts` に細分化。
  - `spsa`: public 171 行 / internal 172 行。WebSocket / API 応答型を包含。
  - `tournament`: public 163 行 / internal 111 行。スタンディングやマッチアップ用型を集約。
  - `instances`: public 148 行 / internal 18 行。REST レスポンス型が中心。

## Ongoing Tasks

1. **肥大化した public 型の整理** — `live` / `spsa` / `tournament` で snake_case と camelCase が混在しているため、正規化層での一本化を検討する。
2. **DashboardLive 名前空間の診断強化** — `LiveNamespaceDiagnostics` のプロバイダメタデータを UI へ露出する。
3. **共有レスポンス型の集約** — `Record<string, unknown>` ベースのレスポンスを `types/shared.ts` に抽出し、モジュール間の重複を減らす。

## Policies

- Window 拡張なしで共有する型は `types/shared.ts` を起点に追加し、用途が限定的ならモジュール内に閉じ込める。
- API 互換性切り替え時は `public.ts` 側で `@deprecated` コメントを追加し、削除計画を Issue 化して追跡する。
- Legacy フィールドは正規化時に即例外を投げ、対応テストを追加する（例: `loses` や snake_case フィールド）。
- 新しいモジュールを追加する場合は `public`/`internal` 構成を必須とし、本ドキュメントに状況を記録する。
