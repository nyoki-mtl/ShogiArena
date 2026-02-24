# Storage API

ストレージ抽象の API リファレンスです。Runner やサービスはこの API を通じてファイル・JSON・DB にアクセスします。

## 概要

Storage API は実行ディレクトリへのアクセスを統一的に提供します。

- **RunStorage**: データクラスによるストレージ実装
- **FilesystemRunStorage**: ファイルシステムベースの永続ストレージ
- **TempRunStorage**: 一時ディレクトリを使用するストレージ

---

## RunStorage クラス

ストレージのデータクラスです。

```python
from shogiarena.arena.storage import RunStorage

@dataclass(slots=True)
class RunStorage:
    ...
```

内部フィールドとして `_db_factory: BaseFactory` を保持します。

### プロパティ

- **run_dir**: `Path` 作業ディレクトリ
- **persistent**: `bool` 永続化するかどうか
- **dashboard_compatible**: `bool` ダッシュボード対応かどうか

### ファイル操作メソッド

#### resolve_path()

```python
def resolve_path(relative_path: str) -> Path
```

相対パスを絶対パスに解決します。

---

#### ensure_dir()

```python
def ensure_dir(relative_path: str) -> Path
```

ディレクトリを作成します。

---

#### exists()

```python
def exists(relative_path: str) -> bool
```

ファイルまたはディレクトリの存在を確認します。

---

#### delete()

```python
def delete(relative_path: str, *, missing_ok: bool = True) -> None
```

ファイルまたはディレクトリを削除します。

- **relative_path**: `str` 対象の相対パス
- **missing_ok**: `bool` (デフォルト: `True`) ファイルが存在しない場合にエラーを無視する

---

#### list_dir()

```python
def list_dir(relative_path: str, *, recursive: bool = False) -> list[Path]
```

ディレクトリ内容を列挙します。

- **relative_path**: `str` 対象ディレクトリの相対パス
- **recursive**: `bool` (デフォルト: `False`) サブディレクトリも再帰的に列挙する

---

### 読み書きメソッド

#### read_json()

```python
def read_json(relative_path: str) -> dict[str, Any] | None
```

JSON ファイルを読み込みます。ファイルが存在しない場合は `None` を返します。

---

#### write_json()

```python
def write_json(relative_path: str, payload: Any, *, indent: int = 2) -> Path
```

JSON ファイルを書き込みます。親ディレクトリを自動作成します。書き込み先の `Path` を返します。

- **relative_path**: `str` 対象ファイルの相対パス
- **payload**: `Any` 書き込む内容
- **indent**: `int` (デフォルト: `2`) インデント幅

---

#### write_text()

```python
def write_text(relative_path: str, content: str) -> Path
```

テキストファイルを書き込みます。親ディレクトリを自動作成します。書き込み先の `Path` を返します。

---

#### append_text()

```python
def append_text(relative_path: str, content: str) -> Path
```

テキストファイルに追記します。親ディレクトリを自動作成します。書き込み先の `Path` を返します。

---

### データベースメソッド

#### db_service()

```python
def db_service() -> ArenaDBService
```

`ArenaDBService` インスタンスを新規作成して返します。

---

### クリーンアップメソッド

#### cleanup()

```python
def cleanup() -> None
```

リソースをクリーンアップします。

---

## FilesystemRunStorage クラス

ファイルシステムベースの永続ストレージです。`RunStorage` のサブクラスです。

```python
from shogiarena.arena.storage import FilesystemRunStorage

FilesystemRunStorage(run_dir: Path)
```

- **run_dir**: `Path` 作業ディレクトリ。存在しない場合は自動作成される

内部で `_db_factory` として `SQLiteShogiDBFactory(run_dir / "game.db")` を生成します。

### 特徴

- `persistent = True`
- `dashboard_compatible = True`
- SQLite DB を `run_dir/game.db` に作成

---

## TempRunStorage クラス

一時ディレクトリを使用するストレージです。`RunStorage` のサブクラスです。

```python
from shogiarena.arena.storage import TempRunStorage

TempRunStorage()
```

引数なしで生成します。一時ディレクトリが自動的に作成されます。

### 特徴

- `persistent = False`
- `dashboard_compatible = True`
- `cleanup()` でディレクトリを削除
- テストやワンショット実行に便利

---

## 使用例

### 基本的な使用

```python
from pathlib import Path

from shogiarena.arena.storage import FilesystemRunStorage

storage = FilesystemRunStorage(Path("output/runs/my_tournament"))

# ディレクトリを作成
storage.ensure_dir("logs")
storage.ensure_dir("games")

# JSON を書き込み
storage.write_json("config.json", {"name": "test", "games": 100})

# JSON を読み込み
config = storage.read_json("config.json")
print(config["name"])

# テキストを書き込み
storage.write_text("logs/run.log", "Started at 2025-01-01\n")
storage.append_text("logs/run.log", "Game 1 completed\n")

# ファイル削除
storage.delete("logs/run.log")

# ディレクトリ一覧
files = storage.list_dir("logs")
```

### DB サービスの使用

```python
from pathlib import Path

from shogiarena.arena.storage import FilesystemRunStorage

storage = FilesystemRunStorage(Path("output/runs/test"))

# DB サービスを取得
db = storage.db_service()

# RecordStore を使って棋譜を保存
from shogiarena.records.storage.db_store import DBRecordStore

store = DBRecordStore(db.get_shogidb())
store.append([record])

# 結果を取得
counts = db.get_game_result_counts()

db.close()
```

### 一時ストレージの使用

```python
from shogiarena.arena.storage import TempRunStorage

storage = TempRunStorage()

try:
    storage.write_json("test.json", {"data": "test"})
    # ...
finally:
    # 一時ディレクトリを削除
    storage.cleanup()
```

### Runner との統合

```python
from pathlib import Path

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import FilesystemRunStorage

config = TournamentRunConfig.from_yaml("tournament.yaml")
storage = FilesystemRunStorage(Path("runs/tournament"))

runner = TournamentRunner(config, storage=storage)
result = runner.run_sync()

print(f"Results saved to: {storage.run_dir}")
```

### パスの解決

```python
from pathlib import Path

from shogiarena.arena.storage import FilesystemRunStorage

storage = FilesystemRunStorage(Path("/home/user/runs/test"))

# 相対パスを絶対パスに解決
abs_path = storage.resolve_path("games/game_001.kif")
print(abs_path)  # /home/user/runs/test/games/game_001.kif

# 存在確認
if storage.exists("config.json"):
    config = storage.read_json("config.json")
```

## 注意事項

- `write_json` / `write_text` は親ディレクトリを自動作成します
- `read_json` はファイルが存在しない場合 `None` を返します
- `write_text`, `append_text`, `write_json` は書き込み先の `Path` を返します
- `TempRunStorage` は `cleanup()` を呼ばないとディレクトリが残ります
- `db_service()` は呼び出しごとに新しい `ArenaDBService` インスタンスを返します

## 関連ドキュメント

- [Runners API](runners.md) - Runner との連携
- [Services API](services.md) - DB サービスの詳細
