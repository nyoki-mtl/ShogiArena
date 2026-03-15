# Engines API

USI エンジン操作の正式な公開モジュールは `shogiarena.engine` です。

## Public Module

```python
from shogiarena.engine import AsyncUsiEngine, UsiThinkRequest, create_engine
```

主な公開シンボル:

- `AnalysisHandle`
- `AsyncUsiEngine`
- `AsyncUsiProcess`
- `AsyncUsiProcessBridgePort`
- `PonderHandle`
- `PonderHitTimings`
- `SpawnerBackedUSIBridge`
- `UsiEngineConfig`
- `UsiEngineStartError`
- `UsiEngineState`
- `UsiMateResult`
- `UsiOption`
- `UsiProtocolParser`
- `UsiThinkPV`
- `UsiThinkRequest`
- `UsiThinkResult`
- `create_engine()`
- `create_engine_from_mapping()`
- `move_from_usi()`

## 最短の使い方

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(byoyomi=1_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

`create_engine()` は `AsyncUsiEngine` を返します。通常は `async with` で使ってください。

## `create_engine()`

```python
async def create_engine(
    config_path: str | Path,
    *,
    timeout: float = 10.0,
    extra_options: JsonObject | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    cpu_affinity: Sequence[int] | None = None,
) -> Any  # returns AsyncUsiEngine at runtime
```

用途:

- engine config YAML から `AsyncUsiEngine` を作る
- default composition root を使って標準配線する
- ローカル実行でも remote instance 利用でも同じ入口にする

## `create_engine_from_mapping()`

```python
async def create_engine_from_mapping(
    config_mapping: Mapping[str, object],
    *,
    timeout: float = 10.0,
    extra_options: JsonObject | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    cpu_affinity: Sequence[int] | None = None,
) -> Any  # returns AsyncUsiEngine at runtime
```

設定ファイルを経由せず、メモリ上の mapping から engine を構築したいときに使います。

## `UsiThinkRequest`

`UsiThinkRequest` は USI の `go` パラメータを表します。

```python
from shogiarena.engine import UsiThinkRequest

UsiThinkRequest(movetime=1_000)
UsiThinkRequest(nodes=1_000_000)
UsiThinkRequest(btime=60_000, wtime=60_000, byoyomi=10_000)
UsiThinkRequest(btime=10_000, wtime=10_000, binc=100, winc=100)
```

主なフィールド:

- `movetime`
- `btime`, `wtime`
- `binc`, `winc`
- `byoyomi`
- `depth`
- `nodes`
- `is_infinite`
- `is_ponder`

## `AsyncUsiEngine`

代表的なメソッド:

- `start()`
- `close()`
- `new_game()`
- `submit_position()`
- `think()`
- `think_mate()`
- `analyze()`
- `stop()`

通常は `async with` で開始と終了を任せれば十分です。

## 解析の例

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        handle = await engine.analyze(
            sfen="startpos",
            request=UsiThinkRequest(is_infinite=True),
        )
        await asyncio.sleep(1.0)
        result = await handle.stop()
        print(result.bestmove if result is not None else None)


asyncio.run(main())
```

## 非公開として扱うもの

- `EngineFactoryService`, `EngineFactoryPort` (`_core.contexts.instances.ports.engine_factory`)
- `_core` 配下の deep import を前提にした利用

内部実装の詳細は [Engine Layers](../technical/engine-layers.md) と [USI Engine](../technical/usi-engine.md) を参照してください。
