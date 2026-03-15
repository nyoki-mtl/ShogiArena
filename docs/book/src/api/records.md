# Records API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。以前の `shogiarena.records` 前提の説明は現行構成と一致しません。現在の record 周辺は `_core.platform.records` と DB 層に分かれています。

ShogiArena の棋譜表現の中心は `rshogi.record.GameRecord` です。その周辺に、format codec、binary writer、manifest 管理、DB 保存が乗っています。

## 構成

- `rshogi.record.GameRecord`
  canonical な棋譜表現
- `_core.platform.records.codec_registry`
  codec capability registry
- `_core.platform.records.codecs`
  組み込み codec 登録
- `_core.platform.records.binary_writer`
  `psv` / `sbinpack` の分割書き出し
- `_core.platform.db.store.record_store`
  SQLite への保存と復元

## `GameRecord`

局面・指し手・metadata・結果は `rshogi` の `GameRecord` に乗せます。ShogiArena 側はこれを直接再定義していません。

利用例:

```python
import rshogi

record = rshogi.record.GameRecord.from_kif_str(kif_text)
payload = record.to_csa()
```

## codec registry

```python
from shogiarena._core.platform.records.codec_registry import (
    PositionStreamExporter,
    RecordReader,
    RecordSerializer,
    get_reader,
    get_serializer,
    get_stream_exporter,
    register_reader,
    register_serializer,
    register_stream_exporter,
)
```

capability は 3 種類に分かれています。

- `RecordSerializer`
  `GameRecord -> bytes | str`
- `RecordReader`
  `bytes | str -> GameRecord`
- `PositionStreamExporter`
  `GameRecord -> Iterator[bytes]`

registry は module-level state です。必要な capability だけ lookup します。

## 組み込み format

[`codecs.py`](/workspaces/ShogiArena/src/shogiarena/_core/platform/records/codecs.py) では次を登録しています。

- `kif`
  serializer / reader
- `csa`
  serializer / reader
- `psv`
  stream exporter
- `sbinpack`
  serializer / reader

`psv` は stream export 前提で、`sbinpack` は各手の `eval` を必要とします。

## `RecordBinaryWriter`

[`RecordBinaryWriter`](/workspaces/ShogiArena/src/shogiarena/_core/platform/records/binary_writer.py) は `psv` / `sbinpack` を複数ファイルへ分割出力する writer です。

設定:

- `format_id`
- `output_dir`
- `max_positions_per_file`
- `max_games_per_file`
- `file_prefix`

主要メソッド:

- `append_record(record)`
- `close()`
- `get_records_summary()`

副作用:

- `records_manifest.json` を更新
- `prefix_00001.psv` のような連番ファイルを書きます

## DB 保存

SQLite 保存は [`DBRecordStore`](/workspaces/ShogiArena/src/shogiarena/_core/platform/db/store/record_store.py) が担当します。これは [DB API](db.md) で扱っています。

## 使用例

```python
from pathlib import Path

import rshogi

from shogiarena._core.platform.records.binary_writer import (
    RecordBinaryWriter,
    RecordBinaryWriterConfig,
)

record = rshogi.record.GameRecord.from_kif_str(kif_text)
writer = RecordBinaryWriter(
    RecordBinaryWriterConfig(
        format_id="psv",
        output_dir=Path(".sandbox/work_dir/records"),
        max_positions_per_file=100000,
        max_games_per_file=None,
        file_prefix="train",
    )
)
writer.append_record(record)
writer.close()
```

## 設計メモ

- record の canonical 形式は `GameRecord` です
- text/binary format の差は codec capability に閉じています
- public facade にしていないのは、records 出力用途が training pipeline 寄りで、まだ安定公開面として切り出していないためです
