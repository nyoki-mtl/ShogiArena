# DB API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。現在、データベース層そのものは public facade に出していません。通常の利用では `RunStorage.db_service()` 経由で触れる前提です。

DB レイヤーは SQLite + SQLAlchemy を使って対局記録、参加エンジン、instance 情報などを保存します。責務は大きく 3 つです。

- connection / session の生成
- `GameRecord` の永続化と読込
- orchestration 向けの高水準 adapter

## 主な型

```python
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.platform.db.store.repository_factory import (
    BaseFactory,
    SQLiteShogiDBFactory,
)
```

## `SQLiteShogiDBFactory`

[`SQLiteShogiDBFactory`](/workspaces/ShogiArena/src/shogiarena/_core/platform/db/store/repository_factory.py) は SQLite 用の repository factory です。

- `db_path: str | Path = ":memory:"`
- `should_echo: bool = False`
- `create() -> ShogiRepository`

`RunStorage` はこれを使って `run_dir / "game.db"` へ接続する factory を持ちます。

## `DBRecordStore`

[`DBRecordStore`](/workspaces/ShogiArena/src/shogiarena/_core/platform/db/store/record_store.py) は `rshogi.record.GameRecord` の保存と復元を担当します。

主要メソッド:

- `append(records, *, should_update=False)`
- `load(*, game_id=None, game_name=None)`

特徴:

- 重複 `game_name` の update 判定を持ちます
- 各指し手の `eval` / `depth` / `nodes` / `wall_time_ms` なども保存します
- 復元時は legality を再確認します

## `ArenaDBAdapter`

[`ArenaDBAdapter`](/workspaces/ShogiArena/src/shogiarena/_core/platform/db/store/arena_db_adapter.py) は run orchestration が使う service layer です。

主要メソッド:

- `ensure_schema_compatibility()`
- `_get_db()` (private; returns `ShogiRepository`)
- `get_games_with_players(game_type="arena")`
- `get_game_id_by_name(game_name)`
- `append_record_list(record_list, *, should_update=False)`
- `upsert_engine_artifact(snapshot)`
- `upsert_instance_spec(snapshot)`
- `record_game_participation(game_id=..., participation=...)`
- `close()`

`ArenaDBAdapter` は lazy に `ShogiRepository` と `DBRecordStore` を初期化します。`RunStorage.db_service()` から返る `DatabaseServicePort` の実体もこれです。

## 典型的な内部利用

```python
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.run_storage import (
    FilesystemRunStorage,
)

storage = FilesystemRunStorage(Path(".sandbox/work_dir/example-run"))
db = storage.db_service()

games = db.get_games_with_players()
```

## 設計メモ

- DB への入口は runner 側から直接 factory を触らず、`RunStorage` に集約します
- `DBRecordStore` は棋譜 I/O に集中し、集計や orchestration 用操作は `ArenaDBAdapter` に寄せています
- public API にしていないのは、永続化スキーマと adapter 境界がまだ変化しやすいためです
