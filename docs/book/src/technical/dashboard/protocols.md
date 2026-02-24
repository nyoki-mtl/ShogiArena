# Dashboard 通信プロトコル指針

このドキュメントは、ダッシュボード各機能の通信方式（WS / SSE / REST）を統一的に判断するための指針をまとめたものです。
「方式を完全に統一する」ことよりも、「選定基準を統一する」ことを優先します。
通信方式に関する記述が他ドキュメントと矛盾する場合は、本ドキュメントを正として扱います。

## 目的

- 機能ごとの通信方式の選定理由を明確化する
- 運用負荷・実装負荷・整合性のバランスを最適化する
- 方式が混在する場合でも一貫性のある説明ができるようにする

## 用語

- **WS**: WebSocket（双方向・低遅延）
- **SSE**: Server-Sent Events / EventSource（単方向・再接続が容易）
- **REST**: JSON API（オンデマンド取得・操作系）
- **スナップショット**: ある時点の完全な状態を返すレスポンス
- **差分更新**: 変化分のみを送るストリームイベント

## 選定基準（統一ルール）

### 1) 更新頻度
- **秒単位で更新される** → **WS**
- **数十秒〜数分の更新でも十分** → **SSE**
- **更新は稀 / 操作時のみ** → **REST**

### 2) 双方向性の要否
- **クライアント→サーバへリアルタイム送信が必要** → **WS**
- **片方向のみで良い** → **SSE**

### 3) 整合性要求
- **欠落が致命的** → **スナップショット + 差分更新**
- **欠落許容** → **ストリームのみ**

### 4) UIのアクティブ性
- **タブが非表示なら更新不要** → 非表示時は切断/停止
- **バックグラウンドでも必要** → 最小頻度で継続

## 共通ルール（設計原則）

### A) 初期ロードはスナップショット（REST）
- 初期状態は必ず REST で取得する
- その後は WS/SSE の差分で追従する

### B) フォールバックは「欠落検知時のみ」
- **TTLだけで定期的に REST を打たない**
- 欠落/不整合が発生したときのみ REST で補完する
- 欠落検知条件はコード上で明文化する
- REST 補完はクールダウンを設け、連続呼び出しを抑制する

### C) ストリームの健全性チェック
- 再接続時は必ず追いつく（last event id / seq / watermark）
- 長時間無更新は「接続が生きているか」を確認

補足: 現行の SSE は基本的にスナップショット配信であり、`Last-Event-ID` による履歴リプレイは行わない。
`seq`/`id` は欠落検知と整合性確認に用いる。

## 欠落検知の標準（SSE/WS 共通）

欠落判定はモジュールごとに定義するが、次の条件を最低限満たすこと。

- **完全性を判定できるメタデータがあること**（total, seq, watermark など）
- **欠落判定の理由がログ/診断に残ること**
- **一時的な欠落と恒久的な欠落を区別すること**

例: 「total が N なのに results が N 未満」「seq が欠番」「expected フィールドが恒常的に欠落」

## SPSA: LTC 結果のフォールバック方針（確定）

### 目的
- SSE が十分なデータを提供できる場合は REST を呼ばない
- 欠落が検知された場合のみ REST を補完として使う
- 欠落が継続する場合でも REST の連続呼び出しを抑制する

### 欠落とみなす条件
- SSE スナップショットの `total` が `results.length` を上回り、要求 limit を満たせない
- SSE ストリームが非アクティブ/切断状態であり、必要データが無い

### フォールバック規約
- 欠落検知時のみ REST を呼ぶ
- 連続 REST はクールダウン（例: 120 秒）を適用
- REST は「整合性回復のための最小限の補完」に限定する
- SSE には `seq` と `id` を付与し、欠落検知と再接続時の追いつきを可能にする
- ストリーム再接続直後は短いウォームアップ猶予を設け、不要な REST を避ける

## 方式別の採用指針

### WS が適切なケース
- ライブ対局など、秒単位の更新
- topic が多く、差分イベントを細かく配信したい
- 既に WS イベント基盤がある

### SSE が適切なケース
- 片方向ストリームで十分
- 中頻度（数十秒〜数分）で良い
- 自動再接続を重視
- 解析・統計系など「逐次通知があれば良い」用途

### REST が適切なケース
- 操作系（start/stop/cancel など）
- 画面表示時だけ取得する静的情報
- 一時的な詳細取得

## 現行マッピング（2026-01-22 時点）

### Live（対局ライブ）
- **方式**: WS（差分） + REST（初期/補助）
- **理由**: 秒単位更新・topic配信・既存 WS 基盤

### Games タブ
- **方式**: REST（初期） + WS（差分イベント）
- **理由**: Live の WS イベントバスと共有できるため
- **補足**: SSEに分けると通信方式が分散し、統一性が低下する

### SPSA タブ
- **方式**: SSE（差分） + REST（初期/欠落時のみ）
- **理由**: 片方向ストリーム・分析用途・中頻度更新
- **現状の改善余地**:
  - TTL で REST を呼ぶ挙動は最小化する
  - 欠落検知時のみフォールバックする設計に寄せる

### Tournament / Engines / Instances
- **方式**: REST中心
- **理由**: 操作系・低頻度更新が中心
- **将来方針**:
  - もしリアルタイム要求が強まるなら WS/SSE に昇格

## 現行との差分（把握済み）

### SPSA: LTC 結果の REST フォールバック
- 現状は TTL に依存した REST リフレッシュが発生しうる
- 目標は「欠落検知時のみ REST」を徹底すること

## 実態マップ（モジュール別・通信経路）

### SPSA
- **SSE**: `/api/spsa/summary/stream`, `/api/spsa/updates/stream`, `/api/spsa/analysis/correlation/stream`, `/api/spsa/analysis/convergence`, `/api/spsa/variant/games/stream`, `/api/spsa/ltc/games/stream`, `/api/spsa/ltc/results/stream`, `/api/spsa/ltc/progress/stream`
- **WS**: `/ws/spsa/updates`（現行の補助。SSE中心に寄せる方針）
- **REST**: `/api/spsa/summary`, `/api/spsa/params`, `/api/spsa/updates`, `/api/spsa/update/{idx}`, `/api/spsa/analysis/correlation`, `/api/spsa/analysis/convergence?format=json`, `/api/spsa/ltc/summary`, `/api/spsa/ltc/results`

### Live
- **WS**: `/ws`（ライブ更新の主経路）
- **REST**: `/api/worker/{idx}`, `/api/game/{id}`, `/api/spsa/game/{id}`, `/api/games?limit=...`, `/api/tournament/match-history?limit=...`
  - `/api/worker/{idx}` は WS スナップショット到着を優先し、短い猶予後に欠落時のみ補完する

### Games / Tournament
- **WS**: `/ws`（`live.games.delta` で一覧差分とスナップショットを配信）
- **SSE**: `/api/tournament/summary/stream`（standings/progress の統合スナップショット）
- **REST**: `/api/games`, `/api/game/{id}`, `/api/standings`, `/api/progress`, `/api/head_to_head`, `/api/tournament/pair-stats`, `/api/tournament/match-history`

### Instances / Engines
- **SSE**: `/api/instances/stream`（初回は全スナップショット、以降は `instances_delta` を優先）
- **REST**: `/api/instances`, `/api/instances/{id}`, `/api/instances/{id}/actions`, `/api/instances/{id}/games`, `/api/instances/{id}/metrics`, `/api/engine_options/{engine_name}`

### Scheduler
- **REST**: `/api/schedule`, `/api/schedule/reschedule`, `/api/schedule/cancel`, `/api/schedule/{game_id}/assign`, `/api/schedule/{game_id}/cancel`, `/api/schedule/{game_id}/restore`

### Diagnostics
- **REST**: `/api/diagnostics/snapshots`, `/api/ws/diagnostics`

## 統一指針に基づく修正候補（優先度順）

### P0: 統一感を壊すリアルタイム更新の混在
- **Games / Tournament / Standings / Progress** を SSE 化検討（一覧/進捗の更新を REST 依存から分離）
- **Instances 一覧** を SSE 化検討（操作は REST のまま、一覧更新のみ SSE）
- **Live の `/api/worker/{idx}` 依存** を削減（WS からの更新を主にし、REST は欠落時のみ）

### P1: SPSA の REST 残存を明文化
- **初期ロード REST**: `/api/spsa/summary`, `/api/spsa/params`, `/api/spsa/updates` は許容
- **詳細取得 REST**: `/api/spsa/update/{idx}` はオンデマンドとして許容
- **分析 REST**: SSE 欠落時のみ許容（TTL起点は禁止）

### P2: Diagnostics の扱い整理
- リアルタイム監視が必要なら SSE/WS 化
- 低頻度で十分なら REST のままで良い（理由を明記）

## REST を残すべき理由（統一説明テンプレ）

次に該当する場合のみ、REST を残してよい。

- **操作系**: start/stop/cancel/assign など（副作用を伴う）
- **オンデマンド詳細**: クリック時のみ必要な詳細（個別ゲーム/更新詳細）
- **低頻度/静的情報**: 画面表示時の一度きりで十分な情報
- **既存の WS/SSE 基盤と整合しない** 明確な理由がある場合

## 修正方針（実装計画）

1) **欠落検知の明確化**
   - SSE が返す `total` と `results.length` の関係を欠落判断の基準に固定
   - 欠落理由を Diagnostics に記録できる形で整理

2) **REST フォールバックの抑制**
   - TTL ベースの定期 REST を禁止
   - 欠落検知時のみ REST を許可し、クールダウンで連続呼び出しを抑制

3) **SSE 完全性の改善（中期）**
   - SSE 送信ペイロードに `seq` / `watermark` を追加
   - 再接続時に追いつける仕様に寄せる

4) **実装確認**
   - Network 監視で REST 発生頻度が欠落時のみになっていることを確認
   - Diagnostics で欠落理由を追跡可能にする

## 例外が必要な場合の明記ルール

方式を統一できない場合は、次の理由を必ず記録します。

- 双方向性が必要
- 既存の WS/SSE 基盤との統合が最適
- 更新頻度が高すぎる / 低すぎる
- 欠落許容度が異なる
- 運用上のコスト差が大きい

## 運用メモ

- プロトコル変更時は、必ず本ドキュメントに理由を追記する
- フォールバック条件を変更したら、欠落検知条件も更新する

## 関連ドキュメント

- **[WebSocket Protocol](websocket-protocol.md)** - WebSocket プロトコルの詳細仕様
- **[SSE Protocol](sse-protocol.md)** - SSE プロトコルの詳細仕様
- **[Dashboard Architecture](architecture.md)** - 全体アーキテクチャ
