# Dashboard Front-end Architecture

## Module Layout

フロントエンドは `src/shogiarena/_core/interfaces/dashboard/frontend/src/modules` 以下で機能単位に分割されています。各機能ディレクトリは次の構造を共有します。

```text
modules/<feature>/
├── components/   # DOM レンダリングと UI ウィジェット
├── services/     # 初期化、API 呼び出し、オーケストレーション
├── state/        # ステートコンテナと定数
├── types/        # 公開/内部 TypeScript 定義
├── utils/        # 純粋ヘルパー
├── __tests__/    # 機能ごとの Vitest
└── index.ts      # 公開 API を再エクスポート (@/modules/<feature> として import)
```

要点:

- `index.ts` がモジュールの公開 API を再エクスポートします。外部インポートは常に `@/modules/<feature>` エイリアスを使い、内部構造の変更に強くします。
- 内部限定の型は `types/internal.ts`（もしくは `types/time.ts` などのドメイン別ファイル）に住みます。`window` 拡張は `src/shogiarena/_core/interfaces/dashboard/frontend/src/types/*.d.ts` 側で管理します。
- 共有のテストヘルパーは `testing/` サブディレクトリにまとめ、プロダクションバンドルから除外します。

## Module Status (2026-02)

既存モジュールは全て上記構造に沿っています。全 11 モジュールがフロントエンドに実装されています。

| Module       | Notes                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| `shared`     | API、ナビゲーション、タブ、通知、DOM ヘルパー、テーマ、ヘッダー進捗、ランタイムモード管理、共有テスト群を収容。 |
| `live`       | `services/time` と `services/updates` をサブドメインとして持ち、`DashboardLiveDiagnostics` で診断を提供。 |
| `games`      | テーブル表示は `components/`、ポーリングは `services/` に配置。                                       |
| `engines`    | 詳細 UI は `components/view.ts`、集計は `services/summary.ts` に分割。                                 |
| `instances`  | `installInstancesModule` を中心に forms/rendering/services/state/utils を分離。Worker を含む。        |
| `tournament` | 順位表・マッチアップ・オープニング・正規化処理を同じパターンで整理。bootstrap で初期化を統合。        |
| `spsa`       | SSE + WebSocket のオーケストレーションを `services/` に統合し、正規化は `services/normalizers.ts`。Worker を含む。 |
| `match`      | 2 エンジンマッチ分析。サマリーと時系列グラフを `services/main.ts` で提供。                            |
| `sprt`       | SPRT 統計テスト。LLR グラフと判定表示を `services/main.ts` で提供。                                   |
| `generate`   | 棋譜生成モード。生成状況の集計サマリーを表示。デフォルトで無効（feature flag: `false`）。              |
| `rules`      | カード表示は `components/render.ts`、状態は `state/index.ts`。                                          |

## Window API Wiring

- それぞれの `install*` サービスは `window.Dashboard*` に API を登録します。依存が欠けていれば例外を投げ、決定的なブートを保証します。
- `modules/shared/services/navigation.ts` は最初にインストールされたナビゲーション API をキャッシュし、以降の呼び出しでは同じ実装を提供します。
- ライブ専用 API は `registerLiveApi` / `requireLiveApi` を通じて調整され、`DashboardLive` が単一のソース・オブ・トゥルースとして機能します。`window.DashboardLive*` は開発者ツール用の読み取り専用ミラーです。

## Live Namespace Dependencies

```text
DashboardCore
  ├─ installLiveTimeModule   → DashboardLive.time / DashboardLiveTime
  │    └─ Consumed by cards, summary, updates
  ├─ installLiveCardsModule  → DashboardLive.cards / DashboardLiveCards
  │    ├─ Consumed by navigation, updates(context), summary
  │    └─ Depends on DashboardLiveTime
  ├─ installLiveSummaryModule → DashboardLive.summary / DashboardLiveSummary
  │    └─ Depends on cards + time
  ├─ installLiveUpdatesModule → DashboardLive.updates / DashboardLiveUpdates
  │    └─ Depends on cards + time
  └─ initializeLiveMain → Requires all APIs above
```

`DashboardLiveDiagnostics` は登録順やプロバイダ、タイムスタンプを記録し、スナップショット更新のたびに `dashboardlive:diagnostics` イベントを window に発火します。

## Error Handling Policy

- 重大な障害は `modules/shared/utils/errors.ts` の `crash` を呼び出し、`DashboardFatalError` を送出すると同時に `DashboardCore` のバナーで可視化します。
- `DashboardCore.warnSoftFailure` は `never` を返し、フォールバックやログだけの握り潰しを防ぎます。
- ネットワーク要求は `modules/shared/services/api.ts` の `requestJson` に集約し、直接 `fetch` を使ってヘッダーやエラー処理をばらつかせないようにします。
- recoverable failure（SSE 切断や一時的な API エラーなど）は `modules/shared/utils/errors.ts` の
  `reportDashboardRecoverableFailure` と `DASHBOARD_RECOVERABLE_FAILURE_EVENT` を通じて Diagnostics へ集約し、
  ユーザー向けメッセージ（Notice やバナー）と開発者向けログを同時に流す方針とします。
  scope は `<Domain>.<Area>.<Kind>` 形式で付け、例として `Tournament.Openings.Refresh` や `Engines.FullOptions.Fetch` のように
  どの領域のどの操作で発生したかが一目で分かる命名を徹底します。

## Real-time Updates

- 通信プロトコルの選定基準と共通ルールは [Dashboard 通信プロトコル指針](protocols.md) を参照してください。
- Live は WebSocket を中心に差分更新を配信します（topic ベースの購読）。
- Games タブは REST でスナップショットを取得し、WS イベント（`live:games-delta`）で差分を反映します。
- SPSA は SSE（EventSource）でストリーム更新を行い、欠落検知時のみ REST で補完します。
- Tournament / Engines / Instances は REST を中心に構成し、必要になった場合のみ WS/SSE に昇格します。

## Documentation Maintenance

- アーキテクチャの変更はこのドキュメントを更新してください。
- 型システムの更新は [Dashboard Typing](typing.md) を参照します。
- テストユーティリティや診断の詳細は [Dashboard Testing](testing.md) に記録します。
- スタイル設計と運用ルールは [Dashboard Styles](styles.md) を参照します。
- 歴史的な背景やタスクは `agent-docs/tasks/` を参照してください。
