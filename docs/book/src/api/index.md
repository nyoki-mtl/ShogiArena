# API Reference

Shogi Arena を Python ライブラリとして使用するための API リファレンスです。

## Formal Public Surface

現在、正式に公開 API として扱うのは次のモジュールです。

| モジュール | 用途 | 主な公開シンボル |
| --- | --- | --- |
| `shogiarena.engine` | USI エンジン操作 | `AnalysisHandle`, `AsyncUsiEngine`, `AsyncUsiProcess`, `AsyncUsiProcessBridgePort`, `PonderHandle`, `PonderHitTimings`, `SpawnerBackedUSIBridge`, `UsiEngineConfig`, `UsiEngineStartError`, `UsiEngineState`, `UsiMateResult`, `UsiOption`, `UsiProtocolParser`, `UsiThinkPV`, `UsiThinkRequest`, `UsiThinkResult`, `create_engine()`, `create_engine_from_mapping()`, `move_from_usi()` |
| `shogiarena.tournament` | tournament / sprt 実行 | `FilesystemRunStorage`, `GameSpec`, `RunStorage`, `TournamentRunConfig`, `TournamentRunner`, `build_tournament_runner()`, `create_run_storage()`, `load_tournament_config()`, `run_tournament()` |
| `shogiarena.cli` | CLI entrypoint | `CliArgumentError`, `CliError`, `build_parser()`, `main()` |
| `shogiarena.composition` | advanced wiring | `DefaultRoot`, `build_default_root()` |

`shogiarena._core.*` は実装正本ですが、公開 API ではありません。

## Quick Start

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine
from shogiarena.tournament import run_tournament


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(byoyomi=1_000),
        )
        print(result.bestmove)

    await run_tournament("tournament.yaml", run_dir="runs/example")


asyncio.run(main())
```

## Public Module Docs

- [Engines](engines.md)
- [Runners](runners.md)
- [CLI](cli.md)

## Internal / Contributor Reference

以下のページは contributor 向けの補足リファレンスです。公開 API ではなく、互換保証もありません。

- [Configs](configs.md)
- [Orchestrators](orchestrators.md)
- [Scheduler](scheduler.md)
- [Execution](execution.md)
- [Services](services.md)
- [Records](records.md)
- [Instances](instances.md)
- [Session](session.md)
- [Storage](storage.md)
- [Dashboard](dashboard.md)
- [DB](db.md)
- [Utils](utils.md)
