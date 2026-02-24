# Arena Dashboard Event API

> **Note: October 2025 Refactor**
>
> 2025 年 10 月のリファクタでグローバル名は `Arena*` から `Dashboard*` に統一され、以下のイベントバス仕様が正式化されました。


## Core State Hub (`DashboardCore`)

`DashboardCore` はダッシュボードの正規ステートを保持し、次のヘルパーを公開します。

- `DashboardCore.state` — モジュール間で共有する `Map` や配列。
- `DashboardCore.events` — 軽量な publish/subscribe バス。
  - `events.on(name, handler)` でリスナーを登録し、破棄関数を返します。
  - `events.off(name, handler)` でリスナーを解除します。
  - `events.emit(name, payload)` でイベントを発火します。例外は `warnSoftFailure` に伝播し、`DashboardFatalError` を投げて UI を即座に停止させます。
- `DashboardCore.mutateState(key, updater)` — ステート更新を共通化します。`updater` は既存値を直接 mutate しても、新しいオブジェクトを返しても構いません。
- `DashboardCore.getStateSlice(keys)` — 指定キーの浅いスナップショットを返します。
- `DashboardCore.registerFetcher(name, fn)` — ライブやトーナメントタブが共有する非同期ローダー（例: ゲーム一覧フィード）を登録します。
- `DashboardCore.state` の各キーは下記のデータ出所に従い、SSE・REST・手動更新のいずれかで維持されます。必ず同じ経路を通して更新し、複数モジュールで別々に正規化しないようにしてください。

### DashboardCore.state data flow

| Key | 主な利用者 | 入力経路 | 備考 |
| --- | --- | --- | --- |
| `workers`, `workerSnapshots`, `boardAdapters` | Live Cards | SSE `worker_update`, `worker_clock`, `worker_snapshot` | `modules/live/services/updates` が一元正規化。直接 mutate せず `DashboardCore.mutateState` 経由。 |
| `liveViewSnapshot` | Live Summary, SPSA Hero, Tournament overview | SSE `summary` / `saved_games` の `liveView` ブロック（REST `/api/live/saved-games` フォールバック時も同スキーマ） | 2025-11 以降は `liveView:update` イベントで購読し、REST で得たスナップショットも `normalizeLiveViewSnapshot` を通す。 |
| `runtimeMode`, `spsaMode` | Tab 切替、SPSA/Tournament Gatekeeper | SSE `summary` (`mode`), REST `/api/spsa/summary` 初期判定 | `applyTabConfiguration` 後は直接書き換え禁止。 |
| `spsaSummary`, `spsaParams`, `spsaEvents` | SPSA タブ | SSE `spsa_updates`、REST `/api/spsa/{summary,params,events}` | `ensureDetailHydration` によりタブ初期表示時に REST でハイドレートし、その後は SSE 差分で更新。 |
| `gamesList`, `gamesSignature*` | Tournament Saved Games | SSE `saved_games`, REST `/api/live/saved-games` | 同期を崩さないよう `liveView` 正規化を通す。 |
| `engineFinalRatings`, `ratingDeltas`, `standingsTCMap` | Tournament standings / charts | REST `/api/tournament/*` | `registerFetcher('tournament:standings', ...)` で共有ロード。 |

### SPSA Dashboard state data flow

| Key | 入力経路 | キャッシュ/再利用ポイント |
| --- | --- | --- |
| `summary`, `summaryCache` | SSE `spsa_summary`（`/api/spsa/summary/stream`）、REST `/api/spsa/summary`（初回/フォールバック） | `SUMMARY_CACHE_TTL_MS`（10s）内は REST 既存値を reuse。SSE で差分適用後は `SPSA_SUMMARY_PROGRESS_EVENT` で DashboardCore と同期。 |
| `params`, `paramsCache` | REST `/api/spsa/params`（`ensureDetailHydration` で遅延起動）、SSE なし | `PARAMS_REFRESH_INTERVAL_MS`（60s）より短いポーリング禁止。 |
| `updates`, `updates.cache`, `updates.progress` | SSE `spsa_updates`, REST `/api/spsa/events`, `/api/spsa/update/{idx}` | 進捗バーは 2025-11 以降 `liveViewSnapshot.progress` を優先し、欠落時のみローカル計算にフォールバック。 |
| `trend`, `convergenceMetrics` | SSE `spsa_convergence`（差分）, REST `/api/spsa/analysis/*` | SSE 受信後 `fetchConvergenceAnalysis` のレスポンスをキャッシュして UI を更新。 |
| `connection` / `visibility` | DOM Visibility API, SSE イベント | `registerVisibilityHandler` が page hidden 状態を Core に伝え、復帰時に SSE 再接続と単発の `refreshAll()` を発火。周回ポーリングは禁止。 |

### LiveView snapshot schema

`liveView` ブロックのフィールドは以下のスキーマに従います。全モジュールはこのスキーマをそのまま参照し、個別の JSON を再解釈しないでください。

| フィールド | 型 | 供給元 | 説明 |
| --- | --- | --- | --- |
| `version` | `number \| null` | SSE 正規化 (`normalizeLiveViewSnapshot`) | 仕様互換性管理用。未指定時は `null`。 |
| `mode` | `'spsa' \| 'tournament' \| 'match' \| 'sprt' \| 'unknown'` | SSE `summary.mode` / REST `/api/*/summary` | ランタイムモードの確定値。 |
| `progress` | `{ kind, unitLabel, completed, total, cancelled, isFinal, state, updatedAt } \| null` | SSE `summary` / `spsa_updates.progress`, REST `/api/live/saved-games` | Live progress バーの唯一のデータソース。`unitLabel` と `state` で UI 表示を統一。 |
| `savedGames` | `{ signature, source, total, limit, count, updatedAt } \| null` | SSE `saved_games`, REST `/api/live/saved-games` | Saved Games ドロップダウンとトーナメント/SPSA 両タブの game list を初期化。 |

> **運用ルール**: REST で取得した `liveView` を手動構築しないでください。`modules/live/services/updates/normalizers.ts` が公開する `normalizeLiveViewSnapshot` を必ず通し、正規化後に `DashboardCore.events.emit('liveView:update', snapshot)` で広報します。

## High-level Events

`modules/live/services/updates` が SSE ペイロードを正規化し、ダッシュボードレベルのイベントとして発行します。利用者は `DashboardCore.events` で購読し、グローバルステートを直接読むことは避けます。

`worker:snapshot`  
:   各ワーカー更新後に発火。ペイロードは `workerIdx` と `snapshot`（最新 SSE ペイロード + カードメタデータ）を含み、ミュータブルな共有を避けるため浅いコピーを保持します。

`worker:clock`  
:   クロックデータの変化で発火。`kind` は `"clock_start"` または `"clock_increment"`、`clock` は正規化済みのミリ秒と表示用ラベルを提供します。

`summary:update`  
:   SSE `summary` ペイロード受信時。レガシーの `window.ARENA_SUMMARY` 参照は廃止されました。

`game:complete`  
:   ワーカーのゲーム完了時に発火。`gameId` と最新ワーカーのスナップショットを含み、cards/history モジュールが利用します。

`spsa:update`  
:   SPSA ダッシュボード向けに予約。現状は REST ポーリングが継続しています。

`dashboard:recoverable-failure`  
:   `reportDashboardRecoverableFailure` を通じて発火される、ダッシュボード全体の「recoverable failure」通知イベント。
    Live Diagnostics HUD（`installLiveDiagnosticsPanel`）はこのイベントを購読し、`scope`（例: `Live.SseSetup.ConnectionError`）、
    `userMessage`、詳細メッセージを「Recoverable Failures」テーブルとして表示します。

### Live View Envelope

`summary` と `saved_games` SSE ペイロードには共通の `liveView` ブロックが追加され、進捗やセーブ済みゲームのメタデータをモード非依存で参照できます。

### Summary progress payload

Summary の進捗は全モードで `games` オブジェクトに統一されています。

```
"games": {
  "completed": 12,
  "total": 40,
  "cancelled": 0
}
```

旧キー（`completedGames` / `totalGames` / `completed` / `total` など）は廃止し、フロントは `games` のみを参照します。

```
"liveView": {
  "version": 1,
  "mode": "spsa",                // "tournament" / "spsa" / "unknown"
  "progress": {
    "kind": "updates",           // "games" or "updates"
    "unitLabel": "updates",
    "completed": 12,
    "total": 40,
    "cancelled": 0,
    "isFinal": false,
    "state": "normal",           // optional visual hint
    "updatedAt": "2025-11-07T12:34:56Z"
  },
  "savedGames": {
    "signature": "abc123",
    "source": "spsa",
    "total": 640,
    "limit": 1000,
    "count": 640,
    "updatedAt": 1762482899000    // epoch millis
  }
}
```

Live View の Progress バーやゲームセレクタはこのブロックのみを読めばよく、SPSA/Tournament で個別の正規化ロジックを持つ必要がありません。

### REST フォールバック

`/api/live/saved-games` (GET) を追加し、最新の `saved_games` スナップショットを REST でも取得できます。SSE が未接続でも同じスキーマ（上記 `liveView` ブロックを含む）が返るため、単一のエンドポイントで Saved Games ドロップダウンを初期化できます。

## Migration Guidance

- Cards は `worker:snapshot`、`worker:clock`、`game:complete` を購読し、`window.ARENA_WORKER_*` はブートストラップ用フォールバックに限定します。
- Summary は `summary:update` と共有フェッチャーに依存します。
- 共有ローダーを追加する場合は必ず `DashboardCore.registerFetcher` を利用して、ネットワーク呼び出しの重複を防ぎます。
- 新しいイベントを導入するときはペイロードを本ドキュメントに記述し、クロスモジュール呼び出しではなく Live オーケストレータから発火してください。

## Diagnostics

`DashboardLiveDiagnostics` はライブ API 登録の順序・提供者・タイムスタンプを記録します。`getLiveNamespaceDiagnostics(window)` で取得できるほか、`installLiveDiagnosticsPanel` が描画する Dev HUD からも参照できます。診断情報は読み取り専用として扱ってください。

### Live Diagnostics alert guidelines

- Dev HUD の Live Diagnostics パネルにはハイドレータ警告と watchlist（ヒートマップ＋スパークライン）が表示されます。閾値はダッシュボード HTML（`index.html`）の `data-live-diagnostics-guidelines` 属性に埋め込まれ、`watchlist.extras[]` の各オブジェクトで `key` / `label` / `unit` / `warning` / `critical` / `notify` を定義できます。`notify: true` を設定した指標は重大度が Warning 以上になると `window.dispatchEvent(new CustomEvent('DashboardDiagnosticsUpdate', ...))` 経由で DevTools/拡張へ通知されます。
- `write_dashboard_assets` は `<run_dir>/live_diagnostics.yml` → `SHOGI_ARENA_LIVE_DIAGNOSTICS_CONFIG` → `.sandbox/configs/run/live_diagnostics.yml` の順に YAML を探索し、ガイドラインを JSON としてインライン化します。
- `auto_snapshot` 設定は `interval_seconds` / `mode`（clipboard|console）に加えて `destination`（console|clipboard|api）と `retention_minutes` を受け付けます。`destination: api` の場合、HUD は `POST /api/diagnostics/snapshots` に JSON を送信し、失敗時は `mode` で指定したフォールバック（clipboard or console）に切り替わります。
- YAML 例:

```yaml
hydrator:
  trigger_per_hour:
    warning: 45
    critical: 60
  failure_rate:
    warning: 0.03
    critical: 0.05
watchlist:
  limit: 6
  extras:
    - key: payloadKb
      label: Payload (KB)
      unit: KB
      warning: 200
      critical: 320
    - key: latencyMs
      label: Hydrator latency
      unit: ms
      warning: 1500
      critical: 3000
    - key: detailPayloadKbSlim
      label: Detail slim payload
      unit: KB
      warning: 180
      critical: 240
    - key: detailPayloadKbFull
      label: Detail full payload
      unit: KB
      warning: 220
      critical: 300
    - key: detailIncludeCount
      label: Detail include count
      unit: fields
      warning: 4
      critical: 6
auto_snapshot:
  interval_seconds: 300
  mode: console
```

- `auto_snapshot` を設定すると HUD が 1 分以上の間隔で `DashboardLiveDiagnostics` を自動ダンプします（`mode: console` の場合は DevTools の console.group へ記録、`mode: clipboard` では書き込み権限が許可されていればクリップボードへ落ちます）。手動 `Export snapshot` ボタンも併用可能です。
- `detailPayloadKbSlim` / `detailPayloadKbFull` / `detailIncludeCount` の Extras を有効にすると、REST/SSE 双方の detail フェッチで計測したサイズと include 数が Sparkline へ流れ、200KB ガイドライン超過を即座に検知できます。

### SPSA detail payload budget

- `fetchUpdateDetail` は取得した JSON のバイト数を計測し、`spsa:hydration:update-detail` メトリクスへ `payloadKb` を送ります。200KB を超えるレスポンスは DevTools コンソールに `Update detail payload … exceeds the 200KB guideline` として警告され、Diagnostics パネルの Extras 列にも最新値・最大値・平均値が表示されます。
- `#spsaTab` の detail stream はデフォルトで `/api/spsa/update/detail/stream?view=slim&include=params,gradients` を開き、REST と同じ slim ポリシーで差分キャッシュを維持します。`data-detail-stream-endpoint`/`data-detail-stream-params` 属性で URL を上書きできます。
- Detail API/SSE は常に `view=slim` を既定値とし、`?include=variant_games,ltc_games,score_history,raw_payload` の CSV で必要セクションを段階取得します。HUD/Ops からは `detailPayloadKbSlim` などの Extras で payload サイズを監視し、200KB ガイドラインを超えた場合は include 群や TTL を再調整してください。
- フロントエンドは「SSE=常時ストリーム」「REST=ユーザー操作時のみ」という役割分担を厳守します。detail SSE が届けるのは Param/Gradients などの軽量フィールドだけで、Variant/LTC/Score History などの重量セクションはユーザーが行を展開した瞬間に限定して REST でバックフィルします。展開していない更新に対しては REST を発火せず、展開済みでも既にキャッシュに同じ include が載っている場合は追加フェッチをスキップします。
- REST で読み込んだセクションは `cacheUpdateDetail` にマージされ、以降の SSE スナップショットでも `loadedIncludes` を維持します。これにより、後続ストリームが slim でも UI 表示は Variant/LTC のリストを保ったまま更新され、不要なバックフィルや再レンダリングを防ぎます。
- Detail SSE 自体もタブ／行の状態に連動します。`data-detail-stream-autostart="0"` を既定とし、Updates タブで行を展開したときのみ `update_idx=<expanded>` を指定した SSE を起動、行を閉じる・タブを離れると接続を停止します。常時モニタリングが必要な環境では `data-detail-stream-autostart="1"` に戻せば従来どおり最新 N 件を Streaming できます。
- `window` クエリを指定すると score history ダイジェストの窓幅を切り替えられます。`window=short`（32 サンプル）を既定とし、`window=long`（128 サンプル）でより長い推移を SSE/REST どちらにも要求できます。`meta.window` に反映されるのでフロントや CLI は使用中のモードを把握できます。
- slim ビューで `score_history` を省略した場合でも `payload.score_history` には `view: digest`・`window_{mean,sigma}`・`samples` などの概要を保持します。フロントは digest を即時に描画しつつ、`include=score_history` を追加フェッチして完全データへ差し替えてください。

## REST/SSE ガイドライン

Live View では SSE を第一優先にしつつ、REST を「初期ハイドレーション／バックフィル」「キャッシュ更新」といった限定用途で利用します。以下の表に主要エンドポイントの方針をまとめます。

| エンドポイント | 主経路 | TTL / キャッシュ | Diagnostics metric | 運用ルール |
| --- | --- | --- | --- | --- |
| `/api/spsa/summary` | SSE `spsa_updates.summary_patch`（常時）、REST は初期描画のみ | `SUMMARY_CACHE_TTL_MS = 10s`。SSE 受信後は REST を呼ばない | `spsa:hydration:summary` | HUD で 45/h を超える High load が出たら `SUMMARY_CACHE_TTL_MS` を伸ばす or `ensureDetailHydration` の遅延トリガーを見直す |
| `/api/spsa/params` | REST（SSE なし） | `PARAMS_REFRESH_INTERVAL_MS = 60s` より短い間隔での再フェッチ禁止。`paramsCache` を reuse | `spsa:hydration:params` | タブをまたいだ使い回しでは cache hit が 80%以上になるよう `ensureDetailHydration` を遅延実行する |
| `/api/games`（Tournament matchups） | REST + `DashboardLive.savedGames` シグネチャ監視 | `MATCHUPS_GAMES_TTL_MS = 90s`。signature が変わるまで `state.gamesListCache` を流用 | `tournament:hydration:matchups` | signature 変化で cache を破棄し、その他の場合は hydrate 1 回のみ。Live Diagnostics で 120/h 以上なら TTL を延長 or `saved_games` SSE で強制更新する |
| `/api/live/saved-games` | SSE `saved_games` でほぼ常時更新。REST は初期ロード & 手動更新時のみ | `savedGames` ブロックが 5 分以内なら再フェッチ不要 | `live:hydration:saved-games` | Saved Games ドロップダウンは `liveViewSnapshot.savedGames` を参照し、REST で得た結果も正規化して同じストアに入れる |
| `/api/worker/{idx}` | SSE `worker_update` が正とし、REST は非同期デバッグ用 | なし（worker snapshot が無ければ 404 想定） | `worker:hydration`（任意） | `shogiarena run tournament` 未実行の run_dir では 404 が正常。`data/workers/*` を再生成する場合は `write_dashboard_assets` を直接呼ぶ |

### 実装メモ

- **SSE 優先**: `DashboardCore.registerFetcher` で共有フェッチャを必ず登録し、タブごとに個別 fetch しない。REST を叩く前に `liveViewSnapshot` や `state.*Cache` を確認し、ヒット時は `recordLiveDiagnosticsMetric(..., { cacheHit: 1 })` を送る。
- **Hydrator 設計**: `createDeferredHydrator` で `ensure(trigger)` を呼ぶときだけ REST を実行し、成功時に TTL を進める。`markDirty()` は signature 変化（`saved_games.signature` など）でのみ呼び出す。
- **閾値管理**: `.sandbox/configs/run/live_diagnostics.yml` の `watchlist.extras` に `/api/games` の payload / latency を追加すれば、HUD 側で高負荷フェッチを可視化できる。新しい指標を追加したら `docs/dashboard/live_api.md` へ反映すること。

### Diagnostics Snapshots API

- `POST /api/diagnostics/snapshots` — HUD や CLI から `{"snapshot": DashboardLiveDiagnostics, "source": "hud", "recordedAt": "..."}` を送信すると `<run_dir>/diagnostics/YYYYMMDD/HHMMSS_xxxx.json` に保存されます。`retention_minutes` (>0) を設定すると古いファイルはサーバ側で自動削除されます。
- 保存された JSON は `snapshot` フィールドに HUD と同じスキーマを保持し、`metadata.receivedAt` などの補足情報を付与します。
- **サーバ設定への反映**: REST 側で TTL を変えた場合は `.sandbox/configs/run/spsa/*.yaml` や tournament config にも同じ値をコメント付きで記載し、実行ログと突き合わせられるようにする。
