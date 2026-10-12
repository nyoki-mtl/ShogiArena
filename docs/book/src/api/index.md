# 公開 API

Pythonから使う関数と型を紹介します。
具体的な使い方は[Pythonライブラリガイド](../user-guide/python-library.md)を参照してください。

## モジュール

| モジュール | 用途 |
| --- | --- |
| `shogiarena.engine` | USI エンジンの起動、思考、解析 |
| `shogiarena.tournament` | トーナメント設定の読み込みと実行 |
| `shogiarena.cli` | CLI エントリポイント |
| `shogiarena.composition` | 実行に必要なサービスの組み立て |

## `shogiarena.engine`

主な入口は次のとおりです。

- `create_engine(config_path, ...)`
- `create_engine_from_mapping(config_mapping, ...)`
- `UsiEngineSession`
- `UsiThinkRequest`
- `UsiThinkResult`
- `UsiThinkPV`
- `UsiEvalValue`
- `UsiIoEvent`
- `EngineLifecycleEvent`
- `EngineProcessInfo`
- `UsiAnalyzeItem`
- `UsiAnalyzePosition`
- `UsiAnalyzeResetPolicy`
- `UsiOption`
- `UsiOptionValidationMode`

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=5_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

`create_engine()`と`create_engine_from_mapping()`は`UsiEngineSession`を返します。
このセッションで、次の操作ができます。

| 用途 | API |
| --- | --- |
| 思考と解析 | `think()`、`UsiThinkRequest`、`UsiThinkResult.select_pv()` |
| 評価値 | `UsiEvalValue.kind`、`value`、`is_cp`、`is_mate`、`as_dict()` |
| USIオプション | `get_usi_options()`、`apply_engine_options()` |
| I/O 診断 | `register_io_log_handler()`、`UsiIoEvent.direction/line/phase/timestamp_ms` |
| プロセスの状態通知 | `register_lifecycle_handler()`、`EngineLifecycleEvent`、`EngineProcessInfo` |
| 固定局面の連続解析 | `iter_analyze_positions()`、`analyze_positions()`、`UsiAnalyzeItem` |

`get_usi_options()` は JSON ではなく `Mapping[str, UsiOption]` を返します。
JSON 化したい場合は、利用側で必要なフィールドを選んで変換してください。

`UsiIoEvent`では、`direction`、`line`、`phase`、`timestamp_ms`などの属性から通信内容を読めます。

イベントハンドラーは対局処理の途中で呼ばれるため、時間のかかる処理を避けてください。

## `shogiarena.tournament`

主な入口は次のとおりです。

- `load_tournament_config(config_source, ...)`
- `run_tournament(config_source, run_dir=...)`
- `create_run_storage(run_dir)`
- `build_tournament_runner(config, storage=...)`
- `TournamentRunConfig`
- `TournamentRunResult`
- `TournamentResults`、`EngineWdlCounts`、`SprtResult`、`SprtDecision`、`JsonValue`

結果に型注釈を付ける場合も `shogiarena.tournament` から import できます。

```python
import asyncio

from shogiarena.tournament import TournamentResults, run_tournament


async def main() -> None:
    result = await run_tournament(
        "tournament.yaml",
        run_dir="runs/example",
    )
    if result is not None:
        tournament: TournamentResults = result.tournament
        print(tournament.get_leaderboard())


asyncio.run(main())
```

`run_tournament()`は正常完了時に`TournamentRunResult`、明示的な中断時に`None`を返します。

SPRT は `sprt` ブロックを含むトーナメント設定として扱います。
SPSAの実行にはCLIの`shogiarena run spsa`を使います。
設定方法は[SPRT / SPSAガイド](../user-guide/spsa.md)を参照してください。

## `shogiarena.composition`

サービスを個別に組み合わせたい場合は、既定の実行環境から各サービスを取得できます。

```python
from shogiarena.composition import build_default_root


root = build_default_root()
engine_runtime = root.engine_runtime
tournament_runtime = root.tournament_runtime
```

エンジン操作やトーナメントの実行だけなら、`shogiarena.engine`と`shogiarena.tournament`の関数を使えます。

## CLI

コマンドラインの詳細は [CLI](cli.md) を参照してください。
