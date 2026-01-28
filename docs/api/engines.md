# Engine API

エンジン操作に関する API のリファレンスです。

## AsyncUsiEngine

非同期で USI エンジンを操作するためのメインクラスです。

```python
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
```

### メソッド

#### `await start() -> None`
エンジンプロセスを起動し、初期化コマンドを送信します。

#### `await close() -> None`
エンジンを終了します（`quit` コマンド）。

#### `await think(sfen, request, moves=None, info_handler=None) -> UsiThinkResult`
指定した局面で思考を開始し、結果を返します。

#### `await think_mate(sfen, ply_limit=None) -> UsiMateResult`
詰め探索を実行します。

#### `await analyze(sfen, request) -> AnalysisHandle`
無限解析を開始します。

## SyncUsiEngine

`AsyncUsiEngine` を同期的に操作するためのラッパーです。Python スクリプトや Jupyter Notebook での利用に適しています。

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
```

### メソッド

#### `from_config_path(path, **kwargs) -> SyncUsiEngine`
設定ファイルからエンジンを生成します。

#### `think(sfen, request) -> UsiThinkResult`
思考を実行し、結果を返します。

## EngineFactory

エンジンの生成とライフサイクルを管理します。

```python
from shogiarena.arena.engines.engine_factory import EngineFactory

# 非同期 API
engine = await EngineFactory.create_engine("config.yaml")
```

## データクラス

### UsiThinkRequest

思考のパラメータを指定します。

| フィールド | 型 | 説明 |
|----------|-----|------|
| `time_ms` | int | 持ち時間 |
| `increment_ms` | int | 加算時間 |
| `byoyomi_ms` | int | 秒読み |
| `node_limit` | int | 探索ノード上限 |
| `depth_limit` | int | 探索深さ上限 |
| `infinite` | bool | 無限解析フラグ |

### UsiThinkResult

思考の結果を保持します。

| フィールド | 型 | 説明 |
|----------|-----|------|
| `bestmove` | str | 最善手 (USI) |
| `score_cp` | int | 評価値 (センチポーン) |
| `nodes` | int | 探索ノード数 |
| `depth` | int | 探索深さ |
| `pv` | list[str] | 読み筋 |