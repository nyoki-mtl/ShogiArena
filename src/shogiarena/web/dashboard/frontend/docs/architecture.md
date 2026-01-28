# Frontend Architecture Overview (2025-10)

## Module Layout

```
modules/<feature>/
├── components/   DOM 構築や表示ロジック
├── services/     初期化・API 呼び出し・主要ロジック
├── state/        状態管理・初期値
├── types/        モジュール内で共有する型定義
├── utils/        純粋関数・ヘルパー
├── __tests__/    モジュール単位のテスト
└── index.ts      公開 API（外部からの唯一の入口）
```

- 外部からの import は `@/modules/<feature>` に統一し、`index.ts` から公開 API を再エクスポートします。
- 内部専用の型は `types/internal.ts` またはサブドメイン別ファイルに置き、Window 拡張などの公開型は `src/types/*.d.ts` で維持します。
- テストはモジュール配下の `__tests__/` に集約し、テスト専用ヘルパーが必要な場合は `testing/` を追加します（バンドル対象外にすること）。
- 例外的にダッシュボード全体のブート順を検証するテストは `src/bootstrap.*.test.ts` に配置しています。増やす際は本ドキュメントと README を更新してください。

## Module Status (2025-10-07)

| モジュール | 状態 | メモ |
|------------|------|------|
| live       | ✅ | 共通レイアウトへ移行済み。公開入口は `@/modules/live` に統一。 |
| instances  | ✅ | `components/`, `services/`, `state/`, `types/`, `utils/`, `__tests__/` を整備済み。 |
| games      | ✅ | レンダリングを `components/`、状態管理を `state/` へ分離。 |
| engines    | ✅ | 詳細表示を `components/view.ts`、サマリ取得を `services/summary.ts` に分割。 |
| rules      | ✅ | カード描画を `components/render.ts`、状態を `state/index.ts` に整理。 |
| spsa       | ✅ | API/イベント処理を `services/`、UI を `components/` に再配置。 |
| tournament | ✅ | 構造を整理し、エントリポイントを `@/modules/tournament` に統一。 |
| shared     | ✅ | API/DOM/通知/Tabs を役割別ディレクトリへ分割。 |

## Window API Wiring

- `install*` 系サービスは初期化時に `window.Dashboard*` へ API を登録します。依存関係を満たせない場合は即座に例外を投げるため、ブート順が崩れてもフェイルファーストで検知できます。
- `modules/shared/services/navigation.ts` など、Window API を再利用するモジュールは再インストール時に既存インスタンスと異なる実装を検出して例外を投げます。再登録は許可されません。
- Live 名前空間は `registerLiveApi` / `requireLiveApi` を経由して登録・取得し、Window 上では getter でミラーリングしています。

## Live Namespace Dependencies

```
DashboardCore
  ├─ installLiveTimeModule   → DashboardLive.time / DashboardLiveTime
  │    └─ 利用: cards, summary, updates
  ├─ installLiveCardsModule  → DashboardLive.cards / DashboardLiveCards
  │    ├─ 利用: navigation, updates(context), summary
  │    └─ 依存: DashboardLiveTime
  ├─ installLiveSummaryModule → DashboardLive.summary / DashboardLiveSummary
  │    └─ 依存: cards + time
  ├─ installLiveUpdatesModule → DashboardLive.updates / DashboardLiveUpdates
  │    └─ 依存: cards + time
  └─ installLiveMain → 上記 API の登録が必須
```

- `setupLiveNamespace` テストヘルパはこの依存順を維持したままモック登録でき、`teardownLiveNamespace` で Window 汚染を防ぎます。
- `DashboardLiveDiagnostics` により、初期化タイムラインとプロバイダのメタデータを可視化できます（`dashboardlive:diagnostics` イベントを発火）。

## Error Handling Policy

- 致命的な例外は `modules/shared/utils/errors.ts` の `crash()` を通じて `DashboardFatalError` として送出します。
- `DashboardCore.warnSoftFailure` は `never` を返す設計です。ウォーニング扱いにせず、例外を投げて UI の不整合を避けます。
- API 呼び出しは `modules/shared/services/api.ts` の `requestJson` に統一し、`fetch` の直接利用は認めません。

## Real-time Updates

- Live 更新は WebSocket (`/ws`) を購読します（SSE `/events` は廃止）。
- Merge Worker は必須で、topic/seq のギャップ検知と `request_snapshot` は Worker 経路で実施します。
- ダッシュボード停止検知は `DashboardCore.notifyDashboardServerStopped` で行います。WebSocket 断は `services/updates` でリトライしつつ、復旧不能時は停止通知に寄せます。
- SPSA 進捗ストリームは `liveView.progress.completed` / `total` のみを受理し、旧フィールドや欠損値は即例外として扱います（フェイルファースト）。

## Documentation Maintenance

- 設計変更やモジュール分割を行った場合は、本ドキュメントと `docs/typing.md` を更新し、`docs/testing.md` にテストヘルパの追加手順を記録します。
- 変更の経緯や判断は `agent-docs/tasks/` に付記し、バックエンドと合意した内容を追跡してください。
