# Dashboard Frontend Testing Guide (Live Modules)

このドキュメントは Live ダッシュボード周辺のテストで利用するユーティリティ `modules/live/testing/setupLiveNamespace` の使い方をまとめたものです。Live 名前空間（`DashboardLive*`）を手動で構築せずに、依存 module のモック登録や初期化順序を安全に検証するためのベストプラクティスを示します。

## 1. ヘルパの概要

- `setupLiveNamespace(overrides?: LiveNamespaceOverrides)`  
  指定した API モックを `registerLiveApi` 経由で Live 名前空間に登録します。省略時は `time` と `cards` を minimum stub で用意します。
- `teardownLiveNamespace()`  
  `resetLiveNamespace` を呼び出し、Window 上に残った `DashboardLive*` / `DashboardLiveDiagnostics` をクリーンアップします。

### LiveNamespaceOverrides の構造

```ts
interface LiveNamespaceOverrides {
    cards?: Partial<LiveCardsApi>;
    time?: Partial<LiveTimeApi>;
    summary?: Partial<LiveSummaryApi>;
    updates?: Partial<LiveUpdatesApi>;
}
```

指定したプロパティは `registerLiveApi` へ渡す前に、最低限必要なメソッドで上書き補完されます（例: `cards.initializeCards` が未定義の場合は no-op stub が注入される）。

## 2. 推奨フロー

```ts
import { beforeEach, afterEach, describe, it } from 'vitest';
import { setupLiveNamespace, teardownLiveNamespace } from '@/modules/live/testing/liveNamespace';

describe('live summary', () => {
    beforeEach(() => {
        setupLiveNamespace({
            time: {
                normalizeSFEN: (sfen) => sfen ?? 'startpos',
                formatTimeControlShort: () => '40/40',
            },
        });
    });

    afterEach(() => {
        teardownLiveNamespace();
    });

    it('updates summary stats when event fires', async () => {
        // テスト対象は requireLiveApi('time') を通じて stub にアクセスする
    });
});
```

ポイント:

- `beforeEach` で必須 API を登録し、`afterEach` で確実にリセットする。
- 追加で必要なメソッドがある場合は `overrides` に差し込む。未指定メソッドは `setupLiveNamespace` 内で例外が投げられるため、依存を洗い出しやすい。
- Live namespace 以外の準備（`DashboardCore` など）は従来通りテスト側で明示的にセットアップする。

## 3. よくある拡張パターン

### Updates モジュールの E2E 風テスト

```ts
setupLiveNamespace({
    cards: {
        updateWorkerOptionLabels: vi.fn(),
        computeAutoViewPly: vi.fn(() => 4),
        syncWorkerViewFromCard: vi.fn(),
        populateSourceDropdown: vi.fn(),
    },
    updates: {
        onWorkerUpdate: vi.fn(),
        setupLiveUpdates: vi.fn(),
    },
});
```

`cards` モックでは `updateWorkerOptionLabels` などの必須メソッドにダミー実装を与えることで、`requireLiveApi('cards')` を通るコードが安全に動作します。`tearDown` を忘れると他テストに `DashboardLiveDiagnostics` が残るため注意。

### 既定 stub の挙動

- `time` 未指定: `normalizeSFEN`・`formatRemain` など主要メソッドを例外スローするスタブで登録。必要なメソッドは overrides で上書きする。
- `cards` 未指定: `initializeCards` は no-op、その他メソッドは明示的に未定義扱い。

## 4. 追加 Tips

- `setupLiveNamespace` は `registerLiveApi` を利用するため、Live モジュール側で導入した依存順チェック（time → cards → summary/updates）をそのまま通過します。順序違反がある場合はテスト時点で例外が投げられます。
- Diagnostics HUD (`installLiveDiagnosticsPanel`) との組み合わせで、Vitest の JSDOM でも `DashboardLiveDiagnostics.timeline` が更新されます。タイムライン検証を行う際は `getLiveNamespaceDiagnostics(window)` を直接参照してください。
- 名前空間を直接書き換えることは避け、必ず `setupLiveNamespace` / `registerLiveApi` 経由で API を差し替えること。

## 5. コーディング規約

- 新しい Live 関連テストを追加する際は、このヘルパを利用することを原則とする。
- 既存テストで Window 直書きを行っている箇所は順次リファクタリング対象とする。変更多数の場合はこのドキュメントへ例外理由を追記し、将来の移行計画を明記する。

## 6. 参考

- `src/modules/live/utils/liveNamespace.ts`: Live namespace の登録ロジックと診断イベントの実装。
- `src/modules/live/testing/liveNamespace.ts`: 本ヘルパのソース。
- `src/modules/live/__tests__/cards.test.ts`: 大規模テストでの実際の利用例。

以上。
