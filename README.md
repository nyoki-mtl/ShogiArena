# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-docs.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-docs.yml)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

> [!NOTE]
> This project is still moving quickly. Public APIs are being clarified, and breaking changes are accepted during development. See [CHANGELOG](CHANGELOG.md) for release notes.

**Documentation:** [https://nyoki-mtl.github.io/ShogiArena/](https://nyoki-mtl.github.io/ShogiArena/)
**Japanese README:** [README_ja.md](README_ja.md)

ShogiArena is a platform for running shogi engine tournaments, statistical testing, dashboard monitoring, and engine automation.

## Public Python API

Supported import paths:

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

Implementation modules live under `shogiarena._core`. They are importable but internal, and not covered by compatibility guarantees.

## Installation

```bash
pip install shogiarena
```

Optional environment initialization:

```bash
shogiarena config init
```

This is useful when you need artifact-based engine resolution, placeholder variables such as `{output_dir}` / `{engine_dir}`, or shared cache settings.

## Quick Examples

### Use a USI engine from Python

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

### Run a tournament from Python

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

### Advanced runner construction

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
    runner = build_tournament_runner(config, storage=storage)
    await runner.run()


asyncio.run(main())
```

## CLI Examples

```bash
shogiarena run tournament configs/run/tournament/example.yaml
shogiarena run sprt examples/configs/run/sprt/example.yaml
shogiarena run spsa examples/configs/run/spsa/example.yaml
```

`tournament` and `sprt` are available both from CLI and the public Python API. `spsa` is currently CLI-first; its Python surface is not yet formalized.

## Documentation

- [Getting Started](https://nyoki-mtl.github.io/ShogiArena/getting-started/)
- [User Guide](https://nyoki-mtl.github.io/ShogiArena/user-guide/)
- [API Reference](https://nyoki-mtl.github.io/ShogiArena/api/)
- [Technical Notes](https://nyoki-mtl.github.io/ShogiArena/technical/)
- [Contributing](https://nyoki-mtl.github.io/ShogiArena/development/contributing/)

## License

MIT. See [LICENSE](LICENSE).
