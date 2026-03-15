# Session API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。正式な公開 API ではありません。利用者向けの import は facade 側を使い、ここで紹介する `_core` 型は runner / orchestrator 実装用と考えてください。

session レイヤーは「1 回の run に紐づく共有状態」と「協調停止フック」を提供します。トーナメント runner と orchestration 層のあいだで共通に使われます。

## 主な型

```python
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.shared.kernel.session_hooks import (
    CallbackGameLifecycleHooks,
    GameCompletionEvent,
    GameLifecycleHooks,
    NoopGameLifecycleHooks,
    SessionStopController,
)
```

## `SessionContext`

[`SessionContext`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/ports/session_context.py) は immutable な実行コンテキストです。保持するのは resume 可能な metadata と run 単位の基礎情報だけで、runtime service 自体は抱えません。

主要フィールド:

- `storage`
  `RunStoragePort` 実装
- `num_workers`
  scheduler / orchestrator が使う worker 数
- `run_id`
  run の識別子
- `instance_pool`
  任意の instance pool 参照
- `metadata`
  resume 可能な JSON 互換メタデータ

### 生成と復元

- `SessionContext.build(...)`
  loosely typed な入力から検証済み context を構築します
- `to_snapshot()`
  `run_id` / `num_workers` / `metadata` を JSON object へ落とします
- `save_to_storage(filename="session_context.json")`
  snapshot を `RunStorage` に保存します
- `from_snapshot(...)`
  検証付きで snapshot から復元します
- `load_from_storage(...)`
  保存済み JSON があれば context を復元します

### バリデーション

- `storage.run_dir` は絶対パスかつ既存ディレクトリである必要があります
- `num_workers >= 1`
- `run_id` は空文字不可
- `metadata` は string key の object に正規化されます

## 停止制御

### `SessionStopController`

[`SessionStopController`](/workspaces/ShogiArena/src/shogiarena/_core/shared/kernel/session_hooks.py) は「これ以上ゲームを新規投入してよいか」を表す最小の協調停止コントローラです。

- `request_stop(reason: str | None = None)`
- `should_continue() -> bool`
- `is_stop_requested`
- `reason`

SPRT 判定や外部停止要求を runner 側から伝播するときに使います。

## 完了イベントと hooks

### `GameCompletionEvent`

1 局終了時に lifecycle hooks へ渡されるイベントです。

- `game_id`
- `game_info`
  `rshogi.record.GameRecord`
- `payload`
  runner 固有の付加情報
- `worker_idx`
- `is_stop_requested`

### `GameLifecycleHooks`

orchestrator が期待する最小 protocol です。

- `on_game_complete(event)`
- `should_continue()`

### `NoopGameLifecycleHooks`

stop controller だけを持つデフォルト実装です。特別な処理が不要な runner でそのまま使えます。

### `CallbackGameLifecycleHooks`

runner ごとのネストした hook class を減らすための汎用実装です。

- payload type を受け取る
- typed callback を 1 つ登録する
- payload 型が違えば `TypeError` にします

## 使用例

```python
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.run_storage import (
    FilesystemRunStorage,
)
from shogiarena._core.contexts.game_session.ports.session_context import (
    SessionContext,
)
from shogiarena._core.shared.kernel.session_hooks import SessionStopController

storage = FilesystemRunStorage(Path(".sandbox/work_dir/example-run"))
context = SessionContext.build(
    storage=storage,
    num_workers=4,
    metadata={"experiment_name": "example"},
)

context.save_to_storage()

stop_controller = SessionStopController()
stop_controller.request_stop(reason="sprt accepted")
```

## 設計メモ

- `SessionContext` は service locator ではありません
- 永続化対象は snapshot 化できる最小情報だけに絞っています
- 停止制御は例外ではなく明示的な state と hook で伝える方針です
