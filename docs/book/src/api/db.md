# DB API

データベース層の API リファレンスです。SQLAlchemy を使った ORM とリポジトリパターンを提供します。

## 概要

DB API は対局データの永続化を担当します。

- **SQLiteShogiDBFactory**: データベース接続のファクトリ（`BaseFactory` を継承）
- **ShogiRepository**: 低レベルリポジトリ
- **ArenaDBService**: 高レベル DB サービス
- **DBRecordStore**: 棋譜の永続化ストア
- **ORM Models**: SQLAlchemy モデル

---

## BaseFactory クラス

データベースファクトリの基底クラスです。

```python
BaseFactory(
    engine: Engine,
    session_factory: ScopedSession[Session],
)
```

- **engine**: `Engine` SQLAlchemy エンジン
- **session_factory**: `ScopedSession[Session]` スコープ付きセッションファクトリ

---

## SQLiteShogiDBFactory クラス

SQLite データベースのファクトリです。`BaseFactory` を継承しています。

```python
from shogiarena.db.factory import SQLiteShogiDBFactory

SQLiteShogiDBFactory(
    db_path: str | Path = ":memory:",
    echo: bool = False,
)
```

- **db_path**: `str | Path` (デフォルト: `":memory:"`) データベースファイルのパス。`":memory:"` でインメモリデータベースを使用
- **echo**: `bool` (デフォルト: `False`) SQL ログを出力するかどうか

#### create()

```python
def create() -> ShogiRepository
```

`ShogiRepository` インスタンスを生成して返します。

### 使用例

```python
from shogiarena.db.factory import SQLiteShogiDBFactory

factory = SQLiteShogiDBFactory("game.db")
db = factory.create()
db.create_tables()
```

---

## ShogiRepository クラス

低レベルのデータベースリポジトリです。

```python
from shogiarena.db.repository import ShogiRepository

ShogiRepository(
    engine: Engine,
    session_factory: ScopedSession[Session],
)
```

- **engine**: `Engine` SQLAlchemy エンジン
- **session_factory**: `ScopedSession[Session]` スコープ付きセッションファクトリ

### プロパティ

- **session**: `Session` SQLAlchemy セッション
- **engine**: `Engine` SQLAlchemy エンジン

### メソッド

#### create_tables()

```python
def create_tables() -> None
```

全テーブルを作成します。

---

#### close_db()

```python
def close_db() -> None
```

データベース接続を閉じます。

---

#### get_record()

```python
def get_record(
    table: type,
    column: str,
    value: Any,
    register: bool = False,
    **kwargs: Any,
) -> Any
```

テーブルから条件に一致するレコードを取得します。

- **table**: `type` 対象テーブルのモデルクラス
- **column**: `str` 検索対象のカラム名
- **value**: `Any` 検索値
- **register**: `bool` (デフォルト: `False`) 見つからない場合にレコードを新規登録するかどうか
- **\*\*kwargs**: `Any` 新規登録時の追加フィールド

---

#### get_player_from_player_name()

```python
def get_player_from_player_name(
    player_name: str,
    game_type: str,
    register: bool = False,
) -> Player | None
```

プレイヤー名からプレイヤーレコードを取得します。

- **player_name**: `str` プレイヤー名
- **game_type**: `str` ゲームタイプ
- **register**: `bool` (デフォルト: `False`) 見つからない場合にレコードを新規登録するかどうか

---

## ArenaDBService クラス

高レベルの DB サービス API です。コンテキストマネージャとして使用できます。

```python
from shogiarena.arena.services.persistence.db_service import ArenaDBService

ArenaDBService(factory: BaseFactory)
```

- **factory**: `BaseFactory` データベースファクトリ

コンテキストマネージャ（`__enter__` / `__exit__`）をサポートしており、`with` 文で使用できます。

### メソッド

#### get_game_result_counts()

```python
def get_game_result_counts(game_type: str = "arena") -> dict
```

ゲーム結果の集計を取得します。

- **game_type**: `str` (デフォルト: `"arena"`) ゲームタイプ

---

#### get_games_with_players()

```python
def get_games_with_players(game_type: str = "arena") -> list
```

プレイヤー情報付きのゲーム一覧を取得します。

- **game_type**: `str` (デフォルト: `"arena"`) ゲームタイプ

---

#### get_all_games()

```python
def get_all_games(
    game_type: str = "arena",
    *,
    offset: int = 0,
    limit: int | None = None,
    search_query: str | None = None,
    player_names: list[str] | None = None,
    result_filter: str | None = None,
) -> list
```

全ゲームを取得します。フィルタリングとページネーションに対応しています。

- **game_type**: `str` (デフォルト: `"arena"`) ゲームタイプ
- **offset**: `int` (デフォルト: `0`) 取得開始位置
- **limit**: `int | None` (デフォルト: `None`) 取得件数の上限
- **search_query**: `str | None` (デフォルト: `None`) 検索クエリ
- **player_names**: `list[str] | None` (デフォルト: `None`) プレイヤー名によるフィルタ
- **result_filter**: `str | None` (デフォルト: `None`) 結果によるフィルタ

---

#### get_game_id_by_name()

```python
def get_game_id_by_name(game_name: str) -> int | None
```

ゲーム名から ID を取得します。

---

#### append_record_list()

```python
def append_record_list(
    record_list: Iterable[rshogi.record.GameRecord | None],
    *,
    update: bool = False,
) -> None
```

棋譜をデータベースに保存します。

- **record_list**: `Iterable[rshogi.record.GameRecord | None]` 保存する棋譜のイテラブル
- **update**: `bool` (デフォルト: `False`) 既存レコードを更新するかどうか

---

#### ensure_schema_compatibility()

```python
def ensure_schema_compatibility() -> None
```

スキーマの互換性を確認し、必要に応じてマイグレーションを行います。

---

#### get_shogidb()

```python
def get_shogidb() -> ShogiRepository
```

内部の `ShogiRepository` インスタンスを取得します。

---

#### get_color_statistics()

```python
def get_color_statistics(game_type: str = "arena") -> dict
```

先手・後手の統計情報を取得します。

- **game_type**: `str` (デフォルト: `"arena"`) ゲームタイプ

---

#### delete_games_by_ids()

```python
def delete_games_by_ids(game_ids: list[int]) -> None
```

ゲーム ID のリストで対局を削除します。

---

#### delete_games_by_names()

```python
def delete_games_by_names(
    game_names: list[str],
    *,
    game_type: str | None = None,
) -> None
```

ゲーム名のリストで対局を削除します。

- **game_names**: `list[str]` 削除対象のゲーム名
- **game_type**: `str | None` (デフォルト: `None`) ゲームタイプによるフィルタ

---

#### upsert_engine_artifact()

```python
def upsert_engine_artifact(snapshot: dict) -> None
```

エンジンアーティファクト情報を挿入または更新します。

---

#### upsert_instance_spec()

```python
def upsert_instance_spec(snapshot: dict) -> None
```

インスタンス仕様情報を挿入または更新します。

---

#### record_game_participation()

```python
def record_game_participation(
    *,
    game_id: int,
    participation: dict,
) -> None
```

対局参加情報を記録します。

- **game_id**: `int` ゲーム ID
- **participation**: `dict` 参加情報

---

#### get_instance_game_history()

```python
def get_instance_game_history(
    instance_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list
```

インスタンスの対局履歴を取得します。

- **instance_id**: `str` インスタンス ID
- **limit**: `int` (デフォルト: `50`) 取得件数の上限
- **offset**: `int` (デフォルト: `0`) 取得開始位置

---

#### close()

```python
def close() -> None
```

セッションを閉じてリソースを解放します。

---

## DBRecordStore クラス

棋譜（`rshogi.record.GameRecord`）の永続化ストアです。`ShogiRepository` を使って棋譜の保存と復元を行います。

```python
from shogiarena.records.storage.db_store import DBRecordStore

DBRecordStore(repository: ShogiRepository)
```

- **repository**: `ShogiRepository` データベースリポジトリ

#### append()

```python
def append(
    records: Iterable[rshogi.record.GameRecord | None],
    *,
    update: bool = False,
) -> None
```

棋譜をデータベースに保存します。

- **records**: `Iterable[rshogi.record.GameRecord | None]` 保存する棋譜のイテラブル
- **update**: `bool` (デフォルト: `False`) 既存レコードを更新するかどうか

---

#### load()

```python
def load(
    *,
    game_id: int | None = None,
    game_name: str | None = None,
) -> rshogi.record.GameRecord | None
```

棋譜をデータベースから復元します。`game_id` または `game_name` のいずれかを指定します。

- **game_id**: `int | None` (デフォルト: `None`) ゲーム ID
- **game_name**: `str | None` (デフォルト: `None`) ゲーム名

---

## ORM Models

### Base

SQLAlchemy のメタデータルートです。

```python
from shogiarena.db.models import Base
```

### Player

プレイヤー情報を保持します。

- **id**: `int` 主キー
- **player_name**: `str` プレイヤー名
- **game_type**: `str` ゲームタイプ

### Game

対局メタデータを保持します。

- **id**: `int` 主キー
- **game_name**: `str` 対局名
- **game_type**: `str` ゲームタイプ
- **start_date**: `datetime` 開始日時
- **end_date**: `datetime` 終了日時
- **result_code**: `int` 結果コード
- **num_moves**: `int` 手数
- **init_position_sfen**: `str` 初期局面

### Kifu

指し手情報を保持します。

- **id**: `int` 主キー
- **game_id**: `int` 外部キー（Game）
- **next_move**: `int` 指し手
- **move_time_ms**: `int` 消費時間
- **eval_value**: `int` 評価値
- **nodes**: `int` ノード数

### EngineArtifact

エンジンアーティファクト情報を保持します。

- **id**: `int` 主キー
- **logical_name**: `str` 論理名
- **artifact**: `str` アーティファクト ID
- **binary_path**: `str` バイナリパス
- **build_flags**: `str` ビルドフラグ

### InstanceSpec

インスタンス情報を保持します。

- **id**: `int` 主キー
- **instance_id**: `str` インスタンス ID
- **display_name**: `str` 表示名
- **cpu_model**: `str` CPU モデル
- **tags**: `str` タグ（JSON）

### GameInstanceParticipation

対局参加記録を保持します。

- **id**: `int` 主キー
- **game_id**: `int` 外部キー（Game）
- **role**: `str` 役割（black/white）
- **engine_name**: `str` エンジン名
- **instance_id**: `str` インスタンス ID

---

## 使用例

### 基本的な使用

```python
from shogiarena.db.factory import SQLiteShogiDBFactory
from shogiarena.records.storage.db_store import DBRecordStore

# ファクトリを作成
factory = SQLiteShogiDBFactory("game.db")
db = factory.create()

# テーブルを作成
db.create_tables()

# レコードの保存は DBRecordStore 経由で行う
store = DBRecordStore(db)
store.append([record])  # GameRecord のリストを保存

# セッションを閉じる
db.close_db()
```

### ArenaDBService の使用

```python
from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.db.factory import SQLiteShogiDBFactory

factory = SQLiteShogiDBFactory("game.db")

with ArenaDBService(factory) as service:
    # ゲーム結果の集計
    counts = service.get_game_result_counts()

    # 全ゲームを取得（ページネーション付き）
    games = service.get_all_games(offset=0, limit=100)

    # 棋譜を保存
    service.append_record_list([record], update=False)
```

### セッションを直接使用

```python
from shogiarena.db.models import Game
from sqlalchemy import select

# クエリ
stmt = select(Game).where(Game.game_type == "arena")
games = db.session.execute(stmt).scalars().all()

for game in games:
    print(f"{game.game_name}: {game.result_code}")
```

### インメモリデータベース

```python
from shogiarena.db.factory import SQLiteShogiDBFactory

# テスト用にインメモリ DB を使用
factory = SQLiteShogiDBFactory(":memory:")
db = factory.create()
db.create_tables()

# テストを実行
# ...

# 自動的にクリーンアップされる
```

## 関連ドキュメント

- [Services API](services.md) - ArenaDBService（高レベル API）
- [Storage API](storage.md) - ストレージとの連携
