# Runners API

正式な公開モジュールは `shogiarena.tournament` です。

## Public Module

```python
from shogiarena.tournament import run_tournament
```

主な公開シンボル:

- `TournamentRunConfig`
- `TournamentRunner`
- `GameSpec`
- `FilesystemRunStorage`
- `create_run_storage()`
- `load_tournament_config()`
- `build_tournament_runner()`
- `run_tournament()`

## 推奨入口: `run_tournament()`

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

これは最も高水準の helper です。

- YAML パスを受け取れる
- mapping を受け取れる
- default composition root を使う
- `TournamentRunner` の組み立てまで隠蔽する

## `load_tournament_config()`

```python
config = load_tournament_config("tournament.yaml")
config = load_tournament_config(mapping_payload)
```

この helper は canonical runtime を通して `TournamentRunConfig` を構築します。`TournamentRunConfig.from_yaml()` のような public loader は用意していないため、設定を Python から読むときはこれを使うのが前提です。

## `build_tournament_runner()`

より細かく制御したい場合は runner を明示的に作れます。

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
        should_skip_resume=True,
    )
    await runner.run()


asyncio.run(main())
```

## `TournamentRunner`

`TournamentRunner` は advanced API です。

```python
TournamentRunner(
    config: TournamentRunConfig,
    *,
    instance_pool: InstancePool | None = None,
    storage: RunStoragePort,
    engine_factory_service: EngineFactoryService,
    init_dashboard_html: InitDashboardHtmlFn,
    api_server_factory: DashboardApiServerFactory,
    progress_reporter: ProgressReporterPort | None = None,
    is_dashboard_enabled: bool | None = None,
    should_skip_resume: bool = False,
)
```

通常の tournament だけでなく、`sprt` セクションを含む設定もこの runner で扱います。つまり、公開面としては `SprtRunner` を別クラスで分けていません。

## `create_run_storage()`

```python
storage = create_run_storage("runs/example")
```

`FilesystemRunStorage` を返します。公開 API ではこれが標準的な run storage です。

## まだ formalize していないもの

- `SpsaRunner`
- `_core` 配下の tournament / spsa deep import

SPRT は `TournamentRunner` に `sprt` セクション付きの設定を渡すことで実行します。独立した `SprtRunner` クラスは存在しません。

SPSA は現状 CLI-first です。Python から使う面は今後別途整理する想定です。
