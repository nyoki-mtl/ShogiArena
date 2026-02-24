# Dashboard WebSocket Protocol

最終更新: 2026-02

このドキュメントは Dashboard の WebSocket プロトコル仕様をまとめます。

## 概要

ダッシュボードの Live 更新は WebSocket `/ws` で配信します。

**実装参照**:
- サーバ: `src/shogiarena/web/dashboard/backend/ws_server.py`
- フロント: `src/shogiarena/web/dashboard/frontend/src/modules/live/`

## LiveEnvelope

サーバは次の共通ラッパで payload を配信します。

```json
{
  "topic": "live.summary.snapshot.tournament",
  "seq": 1,
  "ts": 1730000000000,
  "payload": { /* topic-specific */ }
}
```

**フィールド**:
- `topic`: トピック名（ドット区切り）
- `seq`: topic ごと単調増加（ギャップ検知と復旧に利用）
- `ts`: サーバ時刻（ミリ秒）（任意）
- `payload`: トピック固有のペイロード（JSON-safe、NaN/Infinity を含まない）

## Control Messages (Client → Server)

クライアントからサーバへの制御メッセージ：

### Subscribe

```json
{
  "type": "subscribe",
  "topics": ["live.summary.snapshot.tournament", "live.games.delta"],
  "includeAnalysis": true
}
```

- `topics`: 購読するトピックのリスト（`null` で全購読）
- `includeAnalysis`: 解析情報を含めるかどうか

### Unsubscribe

```json
{
  "type": "unsubscribe",
  "topics": ["live.games.delta"]
}
```

- `topics`: 購読解除するトピックのリスト（省略/`null` で全解除）

### Request Snapshot

```json
{
  "type": "request_snapshot",
  "topic": "live.game.123.moves.diff",
  "fromSeq": 456
}
```

欠落検知時にスナップショットを要求します。

## Topics

### Summary

- **`live.summary.snapshot.<source>`** — サマリーの最新スナップショット
  - `source`: `tournament` / `spsa` / `sprt` / `match`

Summary payloads use a unified progress shape:

```typescript
{
  "games": {
    "completed": number,
    "total": number,
    "cancelled": number
  }
}
```

### Games

- **`live.games.delta`** — スケジュールの bulk/delta 更新

### Assignment

- **`live.assignment.snapshot`** — worker → gid の割当スナップショット
- **`live.assignment.diff`** — worker → gid の割当差分

Payload には `gids` と `assignment_rev` が含まれます。

### Game-specific Topics

- **`live.game.<gid>.snapshot`** — 1ゲームの authoritative snapshot（復旧用）
- **`live.game.<gid>.moves.diff`** — 指し手の加算ログ（欠損不可）
  - フィールド: `ply`, `usi`, `analysis_final`（任意）
- **`live.game.<gid>.analysis.diff`** — 解析情報の最新評価（欠損許容）
- **`live.game.<gid>.state.diff`** — 時計/局面/メタ/終局情報（coalesce/欠損許容）

### Heartbeat

- **`live.heartbeat`** — Keepalive メッセージ

## Assignment Revision

`live.assignment.*` および `live.game.<gid>.*` の payload には **`assignment_rev`**（単調増加整数）が付与されます。

**目的**:
- worker-gid の割当が変更されるたびにインクリメント
- クライアントは snapshot/diff の `assignment_rev` を比較し、古い assignment に基づくデータを破棄可能
- moves.diff の `assignment_rev` が snapshot 取得時より古い場合、クライアントは再 snapshot を要求すべき

## Retention / Memory Management

サーバは長時間稼働でもメモリが増え続けないよう、in-memory 状態に上限を設けます。

### WS Hub

Topic state は TTL/最大件数で自動 prune されます（`ws_server.py`）。

**環境変数**:
- `SHOGI_ARENA_DASHBOARD_WS_TOPIC_TTL_SECONDS`（秒、既定: 1800 / 30分、`0|off|false` で無効化）
- `SHOGI_ARENA_DASHBOARD_WS_MAX_TOPICS`（既定: 20000）

### API Server

Game snapshot キャッシュは LRU で上限化されます（`api_server.py`）。

**環境変数**:
- `SHOGI_ARENA_DASHBOARD_MAX_GAME_SNAPSHOTS`（既定: 512）

## 欠落検知と復旧

### 欠落検知

- クライアントは受信した `seq` をトピックごとに追跡
- `seq` にギャップがある場合は欠落を検知

### 復旧フロー

1. **欠落検知**: 期待する `seq` と実際の `seq` が不一致
2. **スナップショット要求**: `request_snapshot` メッセージを送信
3. **スナップショット受信**: サーバから authoritative snapshot を取得
4. **差分適用**: その後の diff を適用して最新状態に追従

## 実装参照

### サーバサイド

- **WebSocket Hub**: `src/shogiarena/web/dashboard/backend/ws_server.py`
- **スナップショット供給**: `src/shogiarena/web/dashboard/backend/api_server.py`
- **Live 更新**: `src/shogiarena/web/dashboard/backend/live/`

### クライアントサイド

- **スキーマ定義**: `frontend/src/modules/live/services/updates/schema.ts`
- **WebSocket 管理**: `frontend/src/modules/live/services/updates/`
- **Live 名前空間**: `frontend/src/modules/live/`

## 関連ドキュメント

- [Dashboard Protocols](protocols.md) - 通信方式の選定指針
- [Dashboard Architecture](architecture.md) - 全体アーキテクチャ
- [SSE Protocol](sse-protocol.md) - SSE プロトコル仕様
