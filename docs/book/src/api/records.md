# Records API

棋譜データのフォーマット変換・保存ストレージの API リファレンスです。

## 概要

ShogiArena では棋譜データの表現に `rshogi.record.GameRecord` を使用します。
`shogiarena.records` パッケージは GameRecord に対するフォーマット変換（KIF/CSA/psv/sbinpack）と DB 保存機能を提供します。

```python
from shogiarena.records import get_codec, supported_formats
```

## rshogi.record.GameRecord

ShogiArena 全体で使用される棋譜データ型です。`rshogi` ライブラリが提供します。

```python
import rshogi

record = rshogi.record.GameRecord.from_kif(kif_text)
```

GameRecord は以下の機能を持ちます：

- KIF/CSA 形式の棋譜パースとエクスポート
- 盤面の任意手数での取得
- 対局メタデータ（対局者名、結果など）の保持
- 合法性の検証

### パースメソッド

#### `GameRecord.from_kif()`

```python
rshogi.record.GameRecord.from_kif(text: str) -> GameRecord
```

KIF 文字列から GameRecord を生成します。

**引数:**
- **text**: `str` KIF 形式の文字列

**戻り値:**
- `GameRecord`: パースされた棋譜データ

**使用例:**

```python
import rshogi

with open("game.kif", encoding="utf-8") as f:
    kif_str = f.read()
record = rshogi.record.GameRecord.from_kif(kif_str)
print(f"先手: {record.metadata.black_player_name}")
print(f"後手: {record.metadata.white_player_name}")
```

---

#### `GameRecord.from_csa()`

```python
rshogi.record.GameRecord.from_csa(text: str) -> GameRecord
```

CSA 文字列から GameRecord を生成します。

---

#### `GameRecord.read_kif()`

```python
rshogi.record.GameRecord.read_kif(path: str, encoding: str | None = None) -> GameRecord
```

KIF ファイルから GameRecord を生成します。

---

#### `GameRecord.read_csa()`

```python
rshogi.record.GameRecord.read_csa(path: str, encoding: str = "shift_jis") -> GameRecord
```

CSA ファイルから GameRecord を生成します。

---

### エクスポートメソッド

#### `to_kif()`

```python
record.to_kif() -> str
```

GameRecord を KIF 文字列へ変換します。

---

#### `to_csa()`

```python
record.to_csa() -> str
```

GameRecord を CSA 文字列へ変換します。

---

### 盤面取得

#### `get_board()`

```python
record.get_board(move_num: int | None = None) -> Board
```

指定手数の盤面を取得します。`move_num` が `None` の場合は最終局面を返します。

**使用例:**

```python
# 10手目の盤面を取得
board = record.get_board(move_num=10)
print(board.sfen())

# 最終局面を取得
final_board = record.get_board()
```

---

不正な棋譜や不正な値は `ValueError` が送出されます。

## フォーマット変換

### get_codec()

```python
shogiarena.records.get_codec(name: str) -> RecordCodec
```

指定フォーマット名の codec を取得します。

**引数:**
- **name**: `str` フォーマット名（`"kif"`, `"csa"`, `"psv"`, `"sbinpack"`）

**戻り値:**
- `RecordCodec`: フォーマット固有の codec

**使用例:**

```python
from shogiarena.records import get_codec

codec = get_codec("kif")
payload = codec.serialize(record)
restored = codec.deserialize(payload)
```

---

### supported_formats()

```python
shogiarena.records.supported_formats() -> tuple[str, ...]
```

登録済みフォーマットの一覧を返します。

**戻り値:**
- `tuple[str, ...]`: フォーマット名のタプル（例: `("kif", "csa", "psv", "sbinpack")`）

---

### register_codec()

```python
shogiarena.records.register_codec(name: str, codec: RecordCodec) -> None
```

カスタム codec を登録します。

---

### RecordCodec クラス

```python
shogiarena.records.formats.base.RecordCodec
```

フォーマット変換の共通インターフェースです。`serialize` / `deserialize` メソッドを実装します。

> **Note: psv はバイナリ追記専用**
>
> psv は教師局面向けのバイナリ形式で、互換性維持のため**オリジナル準拠**で書き出します。`serialize(record)` は棋譜中の全指し手を局面単位 PSV として連結した bytes を返し、`deserialize()` は未対応です。

## DBRecordStore クラス

```python
shogiarena.records.storage.db_store.DBRecordStore(repository: ShogiRepository)
```

`rshogi.record.GameRecord` を SQLite に保存・復元するストアです。
GameRecord を `Game` / `Kifu` テーブルに分解して永続化します。

### 引数

- **repository**: `ShogiRepository`
  データベースリポジトリ

### メソッド

#### `append()`

```python
store.append(
    records: Iterable[rshogi.record.GameRecord | None],
    *,
    update: bool = False,
) -> None
```

GameRecord のリストを DB に保存します。

**引数:**
- **records**: `Iterable[rshogi.record.GameRecord | None]` 保存する棋譜リスト（`None` はスキップ）
- **update**: `bool` (デフォルト: `False`) `True` の場合、既存レコードを更新

**使用例:**

```python
from shogiarena.db import ShogiRepository
from shogiarena.records.storage.db_store import DBRecordStore

repository = ShogiRepository(...)
store = DBRecordStore(repository)
store.append([record])
```

---

#### `load()`

```python
store.load(
    *,
    game_id: int | None = None,
    game_name: str | None = None,
) -> rshogi.record.GameRecord | None
```

DB から棋譜を読み込みます。`game_id` または `game_name` のいずれかを指定します。

**引数:**
- **game_id**: `int | None` ゲーム ID
- **game_name**: `str | None` ゲーム名

**戻り値:**
- `rshogi.record.GameRecord | None`: 見つかった棋譜、または `None`

---

## バイナリ棋譜の出力

自己対局の psv/sbinpack 出力は `records_output` 設定を通じて有効化します。ファイル切り替えは**対局境界でのみ**行われます。

```yaml
records_output:
  format: sbinpack
  max_positions_per_file: 1000000
  max_games_per_file: 2000
  output_dir: .sandbox/work_dir/records
  file_prefix: selfplay
```

## 使用例

### KIF ファイルを読み込む

```python
import rshogi

with open("game.kif", encoding="utf-8") as f:
    kif_str = f.read()
record = rshogi.record.GameRecord.from_kif(kif_str)

print(f"先手: {record.metadata.black_player_name}")
print(f"後手: {record.metadata.white_player_name}")
print(f"結果: {record.result}")
print(f"手数: {record.num_moves}")
```

### 棋譜をエクスポート

```python
# KIF 形式でエクスポート
kif_output = record.to_kif()
with open("output.kif", "w", encoding="utf-8") as f:
    f.write(kif_output)

# CSA 形式でエクスポート
csa_output = record.to_csa()
```

### codec を使ったフォーマット変換

```python
from shogiarena.records import get_codec, supported_formats

print(supported_formats())  # -> ("kif", "csa", "psv", "sbinpack")

codec = get_codec("kif")
payload = codec.serialize(record)
restored = codec.deserialize(payload)
```

### DB に棋譜を保存

```python
from shogiarena.db.factory import SQLiteShogiDBFactory
from shogiarena.records.storage.db_store import DBRecordStore

factory = SQLiteShogiDBFactory("game.db")
db = factory.create()
db.create_tables()

store = DBRecordStore(db)
store.append([record])

loaded = store.load(game_name="game_001")
```

## 時間表記の互換性

- KIF: `持ち時間：15分` や `持ち時間：15分+60秒` の形式で出力します。先後で時間が異なる場合は `先手持ち時間/後手持ち時間` を出力します。
- CSA: 共通時間は `$TIME_LIMIT` (v2.2 形式) もしくは `$TIME` (v3 形式) のどちらかを出力します。先後差がある場合は `$TIME+/$TIME-` を出力します。

## 関連ドキュメント

- [棋譜フォーマット](../user-guide/configuration.md) - サポートするフォーマット
