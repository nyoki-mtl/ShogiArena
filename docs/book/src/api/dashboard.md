# Dashboard API

最終更新: 2026-02

Web ダッシュボードの API リファレンスです。

## 概要

ダッシュボードは静的アセット・REST API・SSE・WebSocket を組み合わせてライブ進行を表示します。バックエンドは aiohttp ベースの非同期 Python サーバーで、フロントエンドは TypeScript + Vite で構築されています。

## 通信方式の概要

| 機能 | REST | SSE | WebSocket |
|------|------|-----|-----------|
| トーナメント | `/api/standings`, `/api/progress`, `/api/games` 等 | `/api/tournament/summary/stream` | `/ws` |
| SPSA | `/api/spsa/summary`, `/api/spsa/params` 等 | `/api/spsa/summary/stream` 等（10 本） | `/ws/spsa/updates` |
| SPRT | `/api/sprt/summary`, `/api/sprt/timeline` | - | - |
| Match | `/api/match/summary`, `/api/match/timeline` | - | - |
| Generate | `/api/generate/summary`, `/api/generate/games` | - | - |
| インスタンス | `/api/instances` 等（CRUD） | `/api/instances/stream` | - |
| スケジューラ | `/api/schedule` 等 | - | - |
| Live 対局 | `/api/worker/{idx}`, `/api/game/{id}` | - | `/ws` |
| 診断 | `/api/diagnostics/snapshots`, `/api/ws/diagnostics` | - | - |

通信方式の選定指針は [Dashboard 通信プロトコル指針](../technical/dashboard/protocols.md) を参照してください。

## REST API

### コア

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/worker/{worker_idx}` | ワーカーのスナップショット |
| `GET /api/summary` | サマリースナップショット（`?source=tournament\|spsa\|sprt\|match`） |

### トーナメント

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/games` | 対局一覧（`?limit=N` でページネーション） |
| `GET /api/game/{game_id}` | 対局詳細 |
| `GET /api/standings` | 現在の順位表 |
| `GET /api/head_to_head` | ヘッド・ツー・ヘッド統計 |
| `GET /api/progress` | 進捗情報 |
| `GET /api/engine_options/{engine_name}` | エンジンオプション |
| `GET /api/tournament/match-history` | マッチ履歴（`?limit=N`） |
| `GET /api/tournament/pair-stats` | ペア間統計 |

### SPSA

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/spsa/summary` | SPSA サマリー |
| `GET /api/spsa/params` | パラメータ一覧 |
| `GET /api/spsa/events` | SPSA イベント |
| `GET /api/spsa/updates` | 更新一覧 |
| `GET /api/spsa/update/{idx}` | 更新詳細 |
| `GET /api/spsa/variants` | バリアント一覧 |
| `GET /api/spsa/variant/{variant_id}` | バリアント詳細 |
| `GET /api/spsa/games` | SPSA 対局一覧 |
| `GET /api/spsa/game/{game_id}` | SPSA 対局詳細 |
| `GET /api/spsa/analysis/correlation` | パラメータ相関分析 |
| `GET /api/spsa/ltc/summary` | LTC サマリー |
| `GET /api/spsa/ltc/results` | LTC 結果 |

### SPRT

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/sprt/summary` | SPRT サマリー（LLR、判定状態） |
| `GET /api/sprt/timeline` | SPRT 時系列データ |

### Match

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/match/summary` | 2 エンジンマッチのサマリー（勝率、Elo 推定） |
| `GET /api/match/timeline` | マッチ時系列データ |

### Generate

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/generate/summary` | 生成ファイルの集計（`records_manifest.json` 由来） |
| `GET /api/generate/games` | 生成対局の一覧（generate モードでは空配列） |

### インスタンス

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/instances` | インスタンス一覧 |
| `POST /api/instances` | インスタンス作成 |
| `GET /api/instances/{id}` | インスタンス詳細 |
| `PATCH /api/instances/{id}` | インスタンス更新 |
| `DELETE /api/instances/{id}` | インスタンス削除 |
| `POST /api/instances/{id}/actions` | インスタンスアクション（start/stop 等） |
| `GET /api/instances/{id}/games` | インスタンスの対局一覧 |
| `GET /api/instances/{id}/metrics` | インスタンスメトリクス |

### スケジューラ

Runner が有効な場合のみ登録されます。

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/schedule` | スケジュール取得 |
| `POST /api/schedule/reschedule` | リスケジュール |
| `POST /api/schedule/cancel` | 全キャンセル |
| `POST /api/schedule/{game_id}/cancel` | 個別対局キャンセル |
| `POST /api/schedule/{game_id}/restore` | 個別対局復元 |
| `POST /api/schedule/{game_id}/assign` | インスタンスアサイン |

### 診断

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/ws/diagnostics` | WebSocket Hub の診断情報 |
| `POST /api/diagnostics/snapshots` | 診断スナップショット保存 |

## SSE ストリーム

### トーナメント

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/tournament/summary/stream` | トーナメントサマリー（standings/progress の統合更新） |

### SPSA

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/spsa/summary/stream` | SPSA サマリー更新 |
| `GET /api/spsa/updates/stream` | SPSA 更新イベント |
| `GET /api/spsa/update/detail/stream` | 更新詳細（slim ビュー、`?view=slim&include=params,gradients`） |
| `GET /api/spsa/variant/games/stream` | バリアント対局 |
| `GET /api/spsa/analysis/correlation/stream` | パラメータ相関 |
| `GET /api/spsa/analysis/convergence` | 収束分析（集約 SSE） |
| `GET /api/spsa/ltc/results/stream` | LTC 結果 |
| `GET /api/spsa/ltc/progress/stream` | LTC 進捗 |
| `GET /api/spsa/ltc/games/stream` | LTC 対局 |

### インスタンス

| エンドポイント | 説明 |
|---------------|------|
| `GET /api/instances/stream` | インスタンス状態（初回スナップショット + `instances_delta`） |

## WebSocket

### メイン WebSocket Hub

```
ws://localhost:8080/ws
```

LiveEnvelope 形式でメッセージを配信します。

```json
{
  "topic": "live.summary.snapshot.tournament",
  "seq": 1,
  "ts": 1730000000000,
  "payload": { }
}
```

#### 主要トピック

| トピック | 説明 |
|---------|------|
| `live.summary.snapshot.<source>` | サマリースナップショット（`tournament` / `spsa` / `sprt` / `match`） |
| `live.games.delta` | ゲーム一覧の差分更新 |
| `live.assignment.snapshot` | ワーカー → ゲーム割当のスナップショット |
| `live.assignment.diff` | ワーカー → ゲーム割当の差分 |
| `live.game.<gid>.snapshot` | 個別ゲームのスナップショット |
| `live.game.<gid>.moves.diff` | 指し手の差分ログ |
| `live.game.<gid>.analysis.diff` | 解析情報の差分 |
| `live.game.<gid>.state.diff` | 時計/局面/メタ情報の差分 |
| `live.heartbeat` | Keepalive |

#### 制御メッセージ（Client → Server）

```json
{ "type": "subscribe", "topics": ["live.summary.snapshot.tournament"] }
{ "type": "unsubscribe", "topics": ["live.games.delta"] }
{ "type": "request_snapshot", "topic": "live.game.123.moves.diff", "fromSeq": 456 }
```

詳細は [WebSocket プロトコル仕様](../technical/dashboard/websocket-protocol.md) を参照してください。

### SPSA WebSocket

```
ws://localhost:8080/ws/spsa/updates
```

SPSA 更新の補助チャネルです。主経路は SSE を優先し、WS は補助用途で提供されています。

## バックエンドコンポーネント

### ArenaAPIServer クラス

メインのダッシュボードサーバーです。aiohttp Web アプリケーションを管理し、全 API モジュールの統合・共有状態管理・WebSocket Hub の起動を行います。

```python
from shogiarena.web.dashboard.api_server import ArenaAPIServer
```

### DashboardState クラス

共有状態を保持するデータクラスです。

```python
from shogiarena.web.dashboard.backend.state_container import DashboardState
```

### LiveWebSocketHub クラス

WebSocket クライアントへ topic ベースでライブデータを配信します。TTL/最大件数による自動 prune を含みます。

```python
from shogiarena.web.dashboard.backend.ws_server import LiveWebSocketHub
```

### SnapshotStorage クラス

REST/SSE 用にスナップショットを保持・配信します。

```python
from shogiarena.web.dashboard.backend.snapshot_storage import SnapshotStorage
```

### BroadcastHandler クラス

WebSocket 経由のブロードキャスト配信を管理します。レート制限付きです。

```python
from shogiarena.web.dashboard.backend.broadcast import BroadcastHandler
```

### GameSnapshotCache クラス

ゲームスナップショットの LRU キャッシュです。

```python
from shogiarena.web.dashboard.backend.game_cache import GameSnapshotCache
```

### API ハンドラ

| クラス | モジュール | 説明 |
|--------|----------|------|
| `TournamentAPI` | `backend/tournament/api.py` | トーナメント API（standings, games, progress 等） |
| `SPSAAPI` | `backend/spsa/api.py` | SPSA パラメータ最適化 API |
| `SprtAPI` | `backend/sprt/api.py` | SPRT 統計テスト API |
| `MatchAPI` | `backend/match/api.py` | 2 エンジンマッチ分析 API |
| `GenerateAPI` | `backend/generate/api.py` | 棋譜生成モード API |
| `InstancesAPIHandler` | `backend/instances/api.py` | インスタンス CRUD 管理 API |
| `SchedulerAPIHandler` | `backend/scheduler/api.py` | マッチスケジューリング API |

## 環境変数

| 環境変数 | 既定値 | 説明 |
|---------|--------|------|
| `SHOGI_ARENA_DASHBOARD_WS_TOPIC_TTL_SECONDS` | 1800（30分） | WS Hub のトピック TTL（`0`/`off`/`false` で無効化） |
| `SHOGI_ARENA_DASHBOARD_WS_MAX_TOPICS` | 20000 | WS Hub の最大トピック数 |
| `SHOGI_ARENA_DASHBOARD_MAX_GAME_SNAPSHOTS` | 512 | ゲームスナップショット LRU キャッシュ上限 |
| `SHOGI_ARENA_DASHBOARD_INSTANCES_HEALTH_INTERVAL` | 10 | インスタンス健全性チェック間隔（秒） |
| `SHOGI_ARENA_DASHBOARD_HEALTH_CHECK_MIN_INTERVAL` | 5 | ヘルスチェック最小間隔（秒） |

## 使用例

### ダッシュボードサーバーの起動

通常は Runner が自動的に起動しますが、手動で起動する場合:

```python
from pathlib import Path

from shogiarena.web.dashboard.api_server import ArenaAPIServer

server = ArenaAPIServer(
    db_path=Path("output/runs/test/game.db"),
    port=8080,
    run_dir=Path("output/runs/test"),
)
await server.start()
print(f"Dashboard running at http://localhost:{server.port}")
```

CLI からの起動:

```bash
shogiarena dashboard serve --run-dir output/runs/test --port 8080
```

### REST API の呼び出し

```python
import requests

# 順位表を取得
response = requests.get("http://localhost:8080/api/standings")
standings = response.json()

for engine in standings:
    print(f"{engine['name']}: {engine['wins']}W-{engine['losses']}L")
```

### SSE の購読

```python
import sseclient

url = "http://localhost:8080/api/tournament/summary/stream"
response = requests.get(url, stream=True)
client = sseclient.SSEClient(response)

for event in client.events():
    print(f"Update: {event.data}")
```

### WebSocket の接続

```javascript
const ws = new WebSocket('ws://localhost:8080/ws');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(`[${data.topic}] seq=${data.seq}`, data.payload);
};

ws.onopen = () => {
    // トピックを購読
    ws.send(JSON.stringify({
        type: 'subscribe',
        topics: ['live.summary.snapshot.tournament', 'live.games.delta'],
    }));
};
```

## 関連ドキュメント

- [ダッシュボードユーザーガイド](../user-guide/dashboard.md) - 使い方
- [ダッシュボードアーキテクチャ](../technical/dashboard/architecture.md) - 内部構造
- [通信プロトコル指針](../technical/dashboard/protocols.md) - WS/SSE/REST の選定基準
- [SSE プロトコル](../technical/dashboard/sse-protocol.md) - SSE の詳細仕様
- [WebSocket プロトコル](../technical/dashboard/websocket-protocol.md) - WebSocket の詳細仕様
- [Event API](../technical/dashboard/live_api.md) - イベントバスとデータフロー
