# Dashboard Frontend Testing Guide

最終更新: 2026-02

このドキュメントはダッシュボードのテスト運用ルールと、特に Live 名前空間周辺のテストで利用するユーティリティの使い方をまとめます。

## 基本方針

- **テストは機能単位で `modules/<feature>/__tests__` に配置する。**
- **共有テストヘルパーは `modules/<feature>/testing` に集約する。**
- **ブート順序など横断的な検証は `bootstrap.*.test.ts` に置く。**

## Live Namespace Testing Utilities

Live ダッシュボード周辺のテストでは `registerLiveApi` を利用して Live 名前空間（`DashboardLive*`）のモック登録や初期化順序を検証します。`modules/live/testing/` ディレクトリはテストヘルパー置き場として予約されていますが、現時点では空です。テスト側で `registerLiveApi` を直接呼び出してセットアップしてください。

### 1. テストの配置場所

- Live 関連のテストは機能単位で配置します（例: `modules/live/components/cards/__tests__/kifu.test.ts`、`modules/live/components/cards/__tests__/clocks.test.ts`）。
- Live namespace 以外の準備（`DashboardCore` など）はテスト側で明示的にセットアップしてください。

### 2. 追加 Tips

- **Diagnostics HUD** (`installLiveDiagnosticsPanel`) との組み合わせで、Vitest の JSDOM でも `DashboardLiveDiagnostics.timeline` が更新されます。タイムライン検証を行う際は `getLiveNamespaceDiagnostics(window)` を直接参照してください。
- **名前空間を直接書き換えることは避け**、必ず `registerLiveApi` 経由で API を差し替えること。

### 3. コーディング規約

- 既存テストで Window 直書きを行っている箇所は順次リファクタリング対象とする。変更多数の場合はこのドキュメントへ例外理由を追記し、将来の移行計画を明記する。

### 4. 参考

- `src/modules/live/utils/liveNamespace/`: Live namespace の登録ロジックと診断イベントの実装（`core.ts`、`diagnostics.ts`、`index.ts`、`metrics.ts`、`stopwatch.ts`）。
- `src/modules/live/components/cards/__tests__/kifu.test.ts`: カード棋譜テストの利用例。
- `src/modules/live/components/cards/__tests__/clocks.test.ts`: カードクロックテストの利用例。

## 参照先

- モジュール配置方針は [architecture.md](architecture.md) を参照。
- 型設計は [typing.md](typing.md) を参照。
