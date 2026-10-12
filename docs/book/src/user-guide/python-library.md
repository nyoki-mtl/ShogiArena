# Python ライブラリ

ShogiArena は CLI だけでなく Python からも利用できます。
エンジン操作には`shogiarena.engine`、トーナメントの実行には`shogiarena.tournament`を使います。

## エンジンを起動する

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

YAMLファイルの代わりに、辞書で設定を渡すこともできます。

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine_from_mapping


async def main() -> None:
    config = {
        "name": "EngineA",
        "engine_path": "/path/to/engine",
        "options": {"Threads": 2, "USI_Hash": 256},
    }
    async with await create_engine_from_mapping(config) as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(nodes=1_000_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

## 解析結果を読む

`UsiThinkResult` は `bestmove` だけでなく、探索中に受け取った PV を保持します。
代表 PV を選ぶ場合は `select_pv()` を使います。

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(depth=12),
        )
        pv = result.select_pv(depth=12, multipv=1)
        if pv is not None and pv.eval is not None:
            print(pv.eval.kind, pv.eval.value, pv.eval.as_dict())


asyncio.run(main())
```

`UsiEvalValue.kind` は `cp` または `mate` です。
`value`は符号付きのcentipawn値または詰み手数です。

## 固定局面をまとめて解析する

一つのエンジンプロセスで複数の局面を解析するには、`analyze_positions()`を使います。
各局面の前に `usinewgame`、`Clear Hash`、`isready` を入れるかどうかは `UsiAnalyzeResetPolicy` で制御します。

```python
import asyncio

from shogiarena.engine import (
    UsiAnalyzePosition,
    UsiAnalyzeResetPolicy,
    UsiThinkRequest,
    create_engine,
    move_from_usi,
)


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        items = await engine.analyze_positions(
            (
                UsiAnalyzePosition(sfen="startpos"),
                UsiAnalyzePosition(sfen="startpos", moves=(move_from_usi("7g7f"),)),
            ),
            request=UsiThinkRequest(depth=10),
            reset_policy=UsiAnalyzeResetPolicy(
                new_game=True,
                clear_hash_if_available=True,
                isready_before_each=True,
            ),
            failure_policy="collect",
        )
        for item in items:
            if item.result is None:
                print(item.index, "failed", item.error)
                continue
            print(item.index, item.result.bestmove, item.elapsed_ms)


asyncio.run(main())
```

## 診断情報を集める

エンジンのPIDやUSI通信を確認するには、イベントハンドラーを登録します。
`async with`に入る前に登録すると、起動直後のイベントも受け取れます。

```python
import asyncio

from shogiarena.engine import EngineLifecycleEvent, UsiIoEvent, UsiThinkRequest, create_engine


def on_lifecycle(event: EngineLifecycleEvent) -> None:
    if event.name == "process_started" and event.process_info is not None:
        print("pid", event.process_info.pid)


def on_io(event: UsiIoEvent) -> None:
    if event.direction == "stderr":
        print("stderr", event.line)


async def main() -> None:
    engine = await create_engine(
        "engine.yaml",
        collect_info_strings=True,
        collect_raw_io=True,
        collect_stderr=True,
    )
    engine.register_lifecycle_handler(on_lifecycle)
    engine.register_io_log_handler(on_io)

    async with engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=1_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

`UsiIoEvent`の`direction`、`line`、`phase`、`timestamp_ms`から、通信の方向、内容、段階、時刻を確認できます。

## USIオプションを変更する

起動後にオプションを変更するには、`apply_engine_options()`を使います。
指定した値は、エンジンが返すオプション定義に従って検証されます。
YaneuraOuの`BookFile`のように、候補一覧にないファイル名を指定したい場合は、そのオプションだけ検証を緩められます。

```python
await engine.apply_engine_options(
    {"BookFile": "user_book1.db"},
    clear_hash=False,
    validation={"BookFile": "allow_unlisted_combo_value"},
)
```

`get_usi_options()` は `Mapping[str, UsiOption]` を返します。
JSONで保存する場合は、必要な属性を取り出して変換してください。

## トーナメントを実行する

```python
import asyncio

from shogiarena.tournament import run_tournament


async def main() -> None:
    await run_tournament(
        "tournament.yaml",
        run_dir="runs/example",
    )


asyncio.run(main())
```

辞書で設定を渡すこともできます。

```python
import asyncio

from shogiarena.tournament import run_tournament


async def main() -> None:
    await run_tournament(
        {
            "experiment_name": "python-example",
            "engines": [
                {"engine_path": "engine_a.yaml"},
                {"engine_path": "engine_b.yaml"},
            ],
            "tournament": {"scheduler": "round_robin", "games_per_pair": 10},
            "rules": {"time_control": {"time_ms": 10_000, "increment_ms": 100}},
        },
        run_dir="runs/python-example",
    )


asyncio.run(main())
```

## 実行環境を組み立てる

設定や保存先を個別に用意して、トーナメントの実行環境を組み立てることもできます。

```python
import asyncio

from shogiarena.tournament import (
    build_tournament_runner,
    create_run_storage,
    load_tournament_config,
)


async def main() -> None:
    config = load_tournament_config("tournament.yaml")
    storage = create_run_storage("runs/example")
    runner = build_tournament_runner(
        config,
        storage=storage,
        is_dashboard_enabled=False,
    )
    await runner.run()


asyncio.run(main())
```
