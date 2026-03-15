# Python Library Usage

ShogiArena は CLI だけでなく Python ライブラリとしても使えます。
ただし、正式な公開 API は一部のモジュールに限定しています。

## 正式な公開 import

利用者向けとしてサポートする import は次です。

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

`shogiarena._core` は実装本体ですが、内部用です。import できても公開 API ではありません。

## エンジンを使う

現在の公開 API は `AsyncUsiEngine` による非同期エンジンアクセスを提供しています。

### 設定ファイルから起動

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

### マッピングから起動

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine_from_mapping


async def main() -> None:
    config = {
        "engine_path": "/path/to/engine",
        "name": "my-engine",
        "options": {"Threads": 1},
    }
    async with await create_engine_from_mapping(config) as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(nodes=1_000_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

### 複数エンジンを並行に扱う

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def analyze(path: str) -> object:
    async with await create_engine(path) as engine:
        return await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=1_000),
        )


async def main() -> None:
    results = await asyncio.gather(
        analyze("engine1.yaml"),
        analyze("engine2.yaml"),
    )
    for result in results:
        print(result.bestmove)


asyncio.run(main())
```

## トーナメントを実行する

### 最も簡単な入口

`run_tournament()` が最短の public helper です。設定ファイルパスでも mapping でも受け取れます。

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

### mapping から実行

```python
import asyncio

from shogiarena.tournament import run_tournament


async def main() -> None:
    await run_tournament(
        {
            "experiment_name": "example",
            "engines": [
                {"engine_path": "engine1.yaml"},
                {"engine_path": "engine2.yaml"},
            ],
            "tournament": {"games_per_pair": 10, "num_parallel": 2},
            "rules": {"time_control": {"time_ms": 10_000, "increment_ms": 100}},
        },
        run_dir="runs/example",
    )


asyncio.run(main())
```

### advanced API

より細かく制御したい場合は、`TournamentRunner` を組み立てます。

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

`TournamentRunner` は通常の tournament だけでなく、`sprt` セクションを含む設定も扱います。つまり、公開 Python API では `SprtRunner` を別名で公開していません。

## composition root を使う

依存注入や runtime の差し替えが必要なら `shogiarena.composition` を使います。

```python
from shogiarena.composition import build_default_root


root = build_default_root()
engine_runtime = root.engine_runtime
tournament_runtime = root.tournament_runtime
```

これは advanced API です。普通の利用では `shogiarena.engine` / `shogiarena.tournament` の helper を優先してください。

## いま公開していないもの

- `SpsaRunner` の top-level public export
- `shogiarena._core.*` の deep import に対する互換保証

SPRT は `TournamentRunner` に `sprt` セクションを含む設定を渡すことで利用できます（専用の `SprtRunner` クラスはありません）。
SPSA は現状 CLI 中心です。Python 向けの正式公開面は今後整理する想定です。
