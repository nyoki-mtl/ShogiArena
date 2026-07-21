# Python ライブラリ

ShogiArena は CLI だけでなく Python からも利用できます。
公開入口の中心は `shogiarena.engine` と `shogiarena.tournament` です。

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

mapping から直接起動することもできます。

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
`value` は centipawn または詰み手数を符号付きで返すため、`score` 文字列を利用側で再 parse する必要はありません。

## 固定局面をまとめて解析する

単一 engine process を再利用して複数局面を解析する場合は `analyze_positions()` を使います。
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

engine process の PID や USI I/O を確認したい場合は lifecycle handler と I/O handler を登録します。
handler を `async with` に入る前に登録すると、起動直後のイベントも拾えます。

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

`UsiIoEvent` は `direction`、`line`、`phase`、`timestamp_ms` などの typed field を持ちます。
Mapping 風の `dir`、`ts`、`state` という key は公開契約ではないため、利用側で依存しないでください。

## USI option を適用する

起動後に追加で option を送る場合は `apply_engine_options()` を使います。
通常は strict validation が有効です。
YaneuraOu 系の `BookFile` のように、combo option の候補一覧に任意ファイル名が出ない場合だけ、option 単位で検証を緩和できます。

```python
await engine.apply_engine_options(
    {"BookFile": "user_book1.db"},
    clear_hash=False,
    validation={"BookFile": "allow_unlisted_combo_value"},
)
```

`get_usi_options()` は `Mapping[str, UsiOption]` を返します。
JSON snapshot ではないため、保存する場合は必要な field を明示して変換してください。

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

mapping も受け取れます。

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

## Runner を組み立てる

保存先や dashboard 有効化を細かく制御したい場合は、設定と storage を明示して runner を作れます。

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

## 内部実装と未公開の領域

`shogiarena._core` 配下は内部実装です。
開発者向けの説明で登場することはありますが、アプリケーションコードから直接 import しないでください。

SPSA は現時点では CLI 中心で、Python からの公開 API は固定していません。
