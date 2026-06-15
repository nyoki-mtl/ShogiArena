# Python ライブラリ

ShogiArena は CLI だけでなく Python からも利用できます。正式な公開入口は `shogiarena.engine` と `shogiarena.tournament` が中心です。

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

## 公開対象外

`shogiarena._core` 配下は内部実装です。開発者向けの説明で登場することはありますが、アプリケーションコードから直接 import しないでください。

SPSA は現時点では CLI 中心です。Python からの正式な公開 API は固定していません。
