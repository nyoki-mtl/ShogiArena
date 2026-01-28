# Arena Dashboard Live WS Contract

ダッシュボードの Live 更新は WebSocket `/ws` で配信します（`/events` SSE は廃止）。

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

- `seq`: topic ごと単調増加（ギャップ検知と復旧に利用）
- `ts`: サーバ時刻(ms)（任意）
- `payload` は JSON-safe（NaN/Infinity を含まない）であること

## Control messages (client -> server)

- `{"type":"subscribe","topics":[...],"includeAnalysis":true}`（`topics=null` で全購読）
- `{"type":"unsubscribe","topics":[...]}`（省略/`null` は全解除）
- `{"type":"request_snapshot","topic":"live.game.<gid>.moves.diff","fromSeq":123}`

## Retention / Memory

サーバは長時間稼働でもメモリが増え続けないよう、WS 側・API 側の in-memory 状態に上限を設けます。

- WS hub の topic state は TTL/最大件数で自動 prune されます（`ws_server.py`）。
  - `SHOGI_ARENA_DASHBOARD_WS_TOPIC_TTL_SECONDS`（秒、既定: 1800 / 30分、`0|off|false` で無効化）
  - `SHOGI_ARENA_DASHBOARD_WS_MAX_TOPICS`（既定: 20000）
- API server の gid snapshot キャッシュは LRU で上限化されます（`api_server.py`）。
  - `SHOGI_ARENA_DASHBOARD_MAX_GAME_SNAPSHOTS`（既定: 512）

## Topics (代表)

- `live.summary.snapshot.<source>` — summary（常に最新スナップショット、source は tournament/spsa/sprt/match）

Summary payloads use a unified progress shape:

```
"games": { "completed": number, "total": number, "cancelled": number }
```
- `live.games.delta` — schedule の bulk/delta
- `live.assignment.snapshot|diff` — worker_idx -> gid の割当（gids 同梱、`assignment_rev` 付き）
- `live.game.<gid>.snapshot` — 1ゲームの authoritative snapshot（復旧用）
- `live.game.<gid>.moves.diff` — moves（加算ログ、欠損不可。`ply/usi` に加えて `analysis_final`（任意）を載せる）
- `live.game.<gid>.analysis.diff` — analysis（最新評価のみ。欠損許容）
- `live.game.<gid>.state.diff` — state（時計/局面/メタ/終局など、coalesce/欠損許容）
- `live.heartbeat` — keepalive

## assignment_rev

`live.assignment.*` および `live.game.<gid>.*` の payload には `assignment_rev`（単調増加整数）が付与されます。

- サーバで worker-gid の割当が変更されるたびにインクリメント
- クライアントは snapshot/diff の `assignment_rev` を比較し、古い assignment に基づくデータを破棄可能
- moves.diff の `assignment_rev` が snapshot 取得時より古い場合、クライアントは再 snapshot を要求すべき

詳細スキーマは以下を参照:

- サーバ: `src/shogiarena/web/dashboard/ws_server.py`
- スナップショット供給/解決: `src/shogiarena/web/dashboard/api_server.py`
- フロント zod スキーマ: `src/shogiarena/web/dashboard/frontend/src/modules/live/services/updates/schema.ts`
