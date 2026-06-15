# 公開 API

ShogiArena の公開 Python API は、通常利用で必要な入口に絞っています。

## 公開 import

| モジュール | 用途 |
| --- | --- |
| `shogiarena.engine` | USI エンジンの起動、思考、解析 |
| `shogiarena.tournament` | トーナメント設定の読み込みと実行 |
| `shogiarena.cli` | CLI エントリポイント |
| `shogiarena.composition` | 依存注入ルートの高度な利用 |

`shogiarena._core` 配下は内部実装です。import できても互換性は保証されません。

## `shogiarena.engine`

主な入口:

- `create_engine(config_path, ...)`
- `create_engine_from_mapping(config_mapping, ...)`
- `UsiThinkRequest`
- `UsiThinkResult`
- `AsyncUsiEngine`

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

## `shogiarena.tournament`

主な入口:

- `load_tournament_config(config_source, ...)`
- `run_tournament(config_source, run_dir=...)`
- `create_run_storage(run_dir)`
- `build_tournament_runner(config, storage=...)`
- `TournamentRunConfig`

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

SPRT は `sprt` ブロックを含むトーナメント設定として扱います。SPSA の Python API は現時点では正式公開面として固定していないため、CLI からの利用を推奨します。

## `shogiarena.composition`

高度な用途では、既定の runtime wiring を取得できます。

```python
from shogiarena.composition import build_default_root


root = build_default_root()
engine_runtime = root.engine_runtime
tournament_runtime = root.tournament_runtime
```

通常は `shogiarena.engine` と `shogiarena.tournament` の helper を優先してください。

## CLI

コマンドラインの詳細は [CLI](cli.md) を参照してください。
