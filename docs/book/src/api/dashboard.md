# Dashboard API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。ダッシュボード構成は `_core` 内部実装であり、Python library としての正式公開 API ではありません。

ダッシュボードは run 中の状態を `aiohttp` サーバー、snapshot storage、WebSocket hub で配信する仕組みです。利用者向けには CLI から有効化して使う想定で、Python import して細かく組み立てることは主眼にしていません。

## 構成の見方

現行構成は次の 3 層です。

- `contexts.dashboard`
  状態管理、snapshot 差分、broadcast などのアプリケーションロジック
- `interfaces.dashboard`
  HTTP route や WebSocket hub などの I/O 境界
- `contexts.game_session`
  runner 側から流れてくる progress / summary payload

## 主な型

```python
from shogiarena._core.contexts.dashboard.adapters.snapshot_storage import (
    SnapshotStorage,
)
from shogiarena._core.contexts.dashboard.application.broadcast import (
    BroadcastHandler,
)
from shogiarena._core.contexts.dashboard.application.state_container import (
    DashboardState,
)
from shogiarena._core.interfaces.dashboard.api_server.server import (
    ArenaAPIServer,
)
from shogiarena._core.interfaces.dashboard.ws_server import LiveWebSocketHub
```

## `DashboardState`

[`DashboardState`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/dashboard/application/state_container.py) は共有 mutable state の集約です。

保持する主なもの:

- worker snapshot
- worker assignment と revision
- game snapshot cache
- summary snapshot
- games snapshot
- engine option / info snapshot
- game ごとの engine I/O tail

この型自体は単純な container で、差分計算や配信判断は別 component が担います。

## `SnapshotStorage`

[`SnapshotStorage`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/dashboard/adapters/snapshot_storage.py) は summary / games snapshot の保存と差分計算を担当します。

主要メソッド:

- `store_summary(payload, *, source=...)`
- `extract_summary_diff(previous, current)`
- `store_games(payload)`

役割:

- payload validation
- sanitization
- `liveView` 付加
- games delta 計算のための canonical 化

## `BroadcastHandler`

[`BroadcastHandler`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/dashboard/application/broadcast.py) は dashboard 用 event の公開窓口です。

主な責務:

- worker update の反映
- summary update の配信
- games snapshot の publish
- assignment snapshot の publish
- engine I/O tail の仲介

内部では `AssignmentService`、`SummaryGamesMediator`、`WorkerStreamMediator` などへ委譲します。

## `LiveWebSocketHub`

[`LiveWebSocketHub`](/workspaces/ShogiArena/src/shogiarena/_core/interfaces/dashboard/ws_server.py) は `/ws` の topic-based hub です。

特徴:

- bootstrap message 送信
- topic ごとの ring buffer
- snapshot fallback
- analysis payload の間引き/集約
- topic TTL と max topic 数による prune
- diagnostics snapshot

payload は次の envelope で流れます。

```json
{
  "topic": "live.summary.snapshot.tournament",
  "seq": 1,
  "ts": 1730000000000,
  "payload": {}
}
```

## `ArenaAPIServer`

[`ArenaAPIServer`](/workspaces/ShogiArena/src/shogiarena/_core/interfaces/dashboard/api_server/server.py) は `aiohttp` ベースの統合サーバーです。

初期化時に次を束ねます。

- `TournamentAPI`
- `SpsaAPI`
- `MatchAPI`
- `SprtAPI`
- `GenerateAPI`
- `InstancesAPI`
- `SchedulerAPI`
- `LiveWebSocketHub`
- `BroadcastHandler`

route 登録、startup / cleanup、DB 初期化、snapshot copy の façade を持ちます。

## エンドポイント群

route の正本は `ArenaAPIServer` と各 API module です。主なカテゴリだけ挙げると次です。

- `/api/summary`
- `/api/games`, `/api/game/{game_id}`
- `/api/standings`, `/api/progress`
- `/api/spsa/...`
- `/api/sprt/...`
- `/api/instances/...`
- `/api/schedule...`
- `/api/ws/diagnostics`
- `/ws`

詳細な route 名はコード側を参照してください。以前のドキュメントにあった列挙は更新頻度に追従できず、すぐ古くなりやすいため、このページでは構成中心に整理しています。

## 設計メモ

- dashboard は library API というより run-time interface です
- shared mutable state は `DashboardState` へ寄せ、I/O は server / ws hub に閉じています
- public facade にしないのは、フロントエンドと連動して route / payload が変化しやすいためです
