# Session API

セッション制御の API リファレンスです。Runner と Orchestrator が共有するコンテキスト・ライフサイクルフック・停止制御を提供します。

## SessionContext クラス

```python
shogiarena.arena.session.SessionContext(
    storage: RunStorage,
    num_workers: int,
    run_id: str,
    instance_pool: InstancePool | None = None,
    metadata: SessionMetadata | None = None,
    services: SessionServices | None = None,
)
```

実行コンテキストを保持するイミュータブルなデータクラスです（`@dataclass(frozen=True, slots=True)`）。

SessionContext は以下の機能を持ちます：

- 実行に必要なストレージ・ワーカー数・インスタンスプールの一元管理
- メタデータとサービスの保持
- スナップショットとしての保存・復元

### 引数

- **storage**: `RunStorage`
  ストレージ（必須）。`run_dir` は `storage.run_dir` から取得されます。
- **num_workers**: `int`
  ワーカー数（1 以上）。
- **run_id**: `str`
  実行 ID。
- **instance_pool**: `InstancePool | None` (デフォルト: `None`)
  インスタンスプール。
- **metadata**: `SessionMetadata | None` (デフォルト: `None`)
  セッションメタデータ（`TournamentSessionMetadata` または `SpsaSessionMetadata`）。
- **services**: `SessionServices | None` (デフォルト: `None`)
  セッションサービス（`TournamentServices` または `SpsaServices`）。

### メソッド

#### `build()`

```python
SessionContext.build(
    *,
    storage: RunStorage,
    num_workers: int,
    instance_pool: InstancePool | None = None,
    run_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    services: Mapping[str, Any] | None = None,
) -> SessionContext
```

SessionContext を構築するクラスメソッドです。`run_id` が省略された場合は自動生成されます。

**使用例:**

```python
from shogiarena.arena.session import SessionContext
from shogiarena.arena.storage import FilesystemRunStorage

storage = FilesystemRunStorage(Path("output/runs/test"))

ctx = SessionContext.build(
    storage=storage,
    num_workers=4,
    run_id="run_001",
    metadata={
        "experiment_name": "test_tournament",
        "engines": ["engine1", "engine2"],
    },
)

print(f"Run ID: {ctx.run_id}")
```

---

#### `to_snapshot()`

```python
ctx.to_snapshot() -> SessionSnapshot
```

セッション情報をスナップショットとして返します。

---

#### `save_to_storage()`

```python
ctx.save_to_storage(*, filename: str = "session_context.json") -> None
```

セッション情報をストレージに保存します。

---

#### `from_snapshot()`

```python
SessionContext.from_snapshot(
    *,
    storage: RunStorage,
    snapshot: Mapping[str, Any],
    instance_pool: InstancePool | None = None,
    services: Mapping[str, Any] | None = None,
) -> SessionContext
```

スナップショットから SessionContext を復元するクラスメソッドです。

---

#### `load_from_storage()`

```python
SessionContext.load_from_storage(
    *,
    storage: RunStorage,
    instance_pool: InstancePool | None = None,
    services: Mapping[str, Any] | None = None,
    filename: str = "session_context.json",
) -> SessionContext | None
```

ストレージから SessionContext を読み込むクラスメソッドです。ファイルが存在しない場合は `None` を返します。

---

## ライフサイクルフック

### GameCompletionEvent クラス

```python
shogiarena.arena.session.GameCompletionEvent(
    game_id: str,
    game_info: Any,
    payload: Any,
    worker_idx: int | None = None,
    stop_requested: bool = False,
)
```

対局完了イベントを表すデータクラスです。

#### 引数

- **game_id**: `str` 対局 ID
- **game_info**: `Any` 対局情報
- **payload**: `Any` 追加データ
- **worker_idx**: `int | None` (デフォルト: `None`) ワーカーインデックス
- **stop_requested**: `bool` (デフォルト: `False`) 停止が要求されたか

---

### GameLifecycleHooks プロトコル

```python
shogiarena.arena.session.GameLifecycleHooks
```

ライフサイクルフックのプロトコルです。

#### メソッド

#### `on_game_complete()`

```python
async def on_game_complete(self, event: GameCompletionEvent) -> None
```

対局完了時に呼ばれます。**非同期メソッド**です。

---

#### `should_continue()`

```python
async def should_continue(self) -> bool
```

継続すべきか判定します。**非同期メソッド**です。

---

### LifecycleHooksBase クラス

```python
shogiarena.arena.session.LifecycleHooksBase(
    stop_controller: SessionStopController,
)
```

`GameLifecycleHooks` のデフォルト実装です。

#### 引数

- **stop_controller**: `SessionStopController`
  停止コントローラ。

#### プロパティ

- **`stop_controller`**: `SessionStopController` 停止コントローラ

#### メソッド

#### `on_game_complete()`

```python
async def on_game_complete(self, event: GameCompletionEvent) -> None
```

デフォルトでは何もしません。サブクラスでオーバーライドして使用します。

---

#### `should_continue()`

```python
async def should_continue(self) -> bool
```

`stop_controller.should_continue()` の結果を返します。

---

## SessionStopController クラス

```python
shogiarena.arena.session.SessionStopController()
```

協調停止を管理するコントローラです。

### プロパティ

- **`stop_requested`**: `bool` 停止が要求されたか
- **`reason`**: `str | None` 停止理由

### メソッド

#### `request_stop()`

```python
controller.request_stop(*, reason: str | None = None) -> None
```

停止を要求します。

**引数:**
- **reason**: `str | None` (デフォルト: `None`) 停止理由

---

#### `should_continue()`

```python
controller.should_continue() -> bool
```

継続すべきか判定します。停止が要求されていない場合に `True` を返します。

---

#### `assert_running()`

```python
controller.assert_running() -> None
```

停止済みなら `RuntimeError` を送出します。

---

## 使用例

### SessionContext の構築

```python
from pathlib import Path
from shogiarena.arena.session import SessionContext
from shogiarena.arena.storage import FilesystemRunStorage

storage = FilesystemRunStorage(Path("output/runs/test"))

ctx = SessionContext.build(
    storage=storage,
    num_workers=4,
    run_id="run_001",
    metadata={
        "experiment_name": "test_tournament",
        "engines": ["engine1", "engine2"],
    },
)

print(f"Run ID: {ctx.run_id}")
```

### ライフサイクルフックの実装

```python
from shogiarena.arena.session import LifecycleHooksBase, GameCompletionEvent, SessionStopController

class MyHooks(LifecycleHooksBase):
    def __init__(self):
        super().__init__(SessionStopController())
        self.completed_games = []

    async def on_game_complete(self, event: GameCompletionEvent) -> None:
        self.completed_games.append(event.game_id)
        print(f"Game {event.game_id} completed")

        # 10局完了したら停止
        if len(self.completed_games) >= 10:
            self.stop_controller.request_stop(reason="10 games completed")

hooks = MyHooks()
```

### 停止制御

```python
from shogiarena.arena.session import SessionStopController

controller = SessionStopController()

# 別スレッド/タスクから停止を要求
controller.request_stop(reason="User requested stop")

# Orchestrator 内でチェック
if not controller.should_continue():
    print(f"Stopping: {controller.reason}")
```

### Runner との統合

```python
from shogiarena.arena.session import LifecycleHooksBase, SessionStopController
from shogiarena.arena.services.statistics.sprt import SprtDecision

class TournamentHooks(LifecycleHooksBase):
    def __init__(self, sprt_service, max_games):
        super().__init__(SessionStopController())
        self.sprt_service = sprt_service
        self.max_games = max_games
        self.game_count = 0

    async def on_game_complete(self, event):
        self.game_count += 1

        # SPRT 判定
        if self.sprt_service:
            result = self.sprt_service.add_game_result(event.game_info.result)
            if result.decision != SprtDecision.CONTINUE:
                self.stop_controller.request_stop(reason=f"SPRT: {result.decision}")

        # 最大対局数
        if self.game_count >= self.max_games:
            self.stop_controller.request_stop(reason="Max games reached")
```

## 関連ドキュメント

- [Runners API](runners.md) - Runner との連携
- [Orchestrators API](orchestrators.md) - Orchestrator との連携
