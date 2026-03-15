# Storage API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。正式な公開 API は `shogiarena.engine` / `shogiarena.tournament` / `shogiarena.cli` / `shogiarena.composition` を基準にしてください。ここで扱う `RunStorage` は `_core` の実装詳細です。

実行単位の成果物を置くストレージ抽象です。トーナメント runner やダッシュボード、DB 永続化はこの境界を通じて `run_dir` 配下へアクセスします。

## 位置づけ

- 利用者向けコードでは通常 `shogiarena.tournament.create_run_storage()` や `run_tournament()` を使います
- 内部では [`RunStorage`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/adapters/run_storage.py) と `RunStoragePort` が session 系の共通 I/O 境界になります
- 現在の実装はファイルシステム前提で、メモリ実装や一時実装はありません

## 実装

```python
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.run_storage import (
    FilesystemRunStorage,
    RunStorage,
)
```

### `RunStorage`

`run_dir` と DB factory を保持する最小のストレージ実装です。

- `run_dir: Path`
  実行ディレクトリ。絶対パスで扱われます。
- `db_service() -> DatabaseServicePort`
  `ArenaDBAdapter` を lazy に生成して返します。
- `resolve_path(relative_path: str) -> Path`
  `run_dir` 基準の絶対パスへ解決します。
- `read_json(relative_path: str) -> dict[str, object] | None`
  JSON object を読み込みます。ファイルがなければ `None` を返します。
- `write_json(relative_path: str, payload: JsonValue, *, indent: int = 2) -> Path`
  親ディレクトリを作って JSON を保存します。

`RunStorage` は「便利ユーティリティ集」ではなく、session / dashboard / DB が共通で必要とする最小 I/O だけを持つ設計です。

### `FilesystemRunStorage`

永続ディレクトリをそのまま `run_dir` にする実装です。

- コンストラクタで `run_dir` を作成します
- SQLite DB は `run_dir / "game.db"` に作られます
- `RunStorage` の API 以外は増やしていません

## 内部 port

session 系から見える契約は `RunStoragePort` です。

```python
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
```

必要条件は次だけです。

- `run_dir: Path`
- `db_service()`
- `read_json()`
- `write_json()`

runner や session context はこの port に依存し、具体実装として `FilesystemRunStorage` を受け取ります。

## 使用例

```python
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.run_storage import (
    FilesystemRunStorage,
)

storage = FilesystemRunStorage(Path(".sandbox/work_dir/example-run"))
storage.write_json("meta/session.json", {"run_id": "example-run"})

payload = storage.read_json("meta/session.json")
db = storage.db_service()
```

## 設計メモ

- `RunStorage` は run-scoped artifact の集約点です
- テキスト書き込みや削除などの広い FS API はここにはありません
- DB への入口は `db_service()` に閉じ、呼び出し側が直接 `SQLiteShogiDBFactory` を触る場面は減らしています
