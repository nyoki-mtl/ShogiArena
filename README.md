# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/docs.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/docs.yml)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

> **📖 [Full Documentation](https://nyoki-mtl.github.io/ShogiArena/)** | [日本語ドキュメント](README_ja.md)

**ShogiArena** is a comprehensive platform for shogi engine development and evaluation. It provides:

- **Python Wrapper for USI Engines**: Easy-to-use synchronous and asynchronous interfaces (`SyncUsiEngine`, `AsyncUsiEngine`) for integrating any USI-compatible shogi engine into your Python projects
- **Advanced Tournament Environment**: Flexible tournament execution with customizable rules, time controls, adjudication, and parallel game execution (Round-robin, SPRT, SPSA)
- **Real-time Dashboard**: Web-based interface for monitoring live games, analyzing statistics, and visualizing rating evolution
- **Engine Parameter Tuning**: Automated optimization using SPSA with gradient-based search and statistical validation via SPRT

## Demo

https://github.com/user-attachments/assets/1cdebe23-b1a9-4d8e-91c0-f56ca970b569

*Live tournament monitoring with real-time updates and interactive dashboard*

## Installation

```bash
pip install shogiarena
```

For development installation, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Configuration (Optional)

While not required, running `shogiarena init` is recommended for:

```bash
shogiarena init
```

**What it configures:**
- **Output directories**: Centralized location for tournament results and databases
- **Engine cache**: Shared storage for engine binaries across projects
- **Repository integration**: Access to engine repositories (e.g., YaneuraOu) via artifact references
- **Placeholder support**: Use `{output_dir}` and `{engine_dir}` in configs

**What you can do without it:**
- ✅ Use Python API (`SyncUsiEngine`, `AsyncUsiEngine`) with direct paths
- ✅ Run tournaments with absolute paths in configs
- ✅ Everything works with default paths (`./shogiarena_output`, temp directories)

**What requires configuration:**
- ❌ Artifact references (e.g., `artifact: yaneuraou@main:YaneuraOu-by-gcc`)
- ❌ Placeholder variables in configs (e.g., `path: "{engine_dir}/myengine"`)
- ❌ Private GitHub repository access (requires `github_token` in settings)

See [detailed setup guide](https://nyoki-mtl.github.io/ShogiArena/getting-started/quick-start/) for more options.

## Quick Examples

### Example 1: Use Engine in Python

The simplest way to integrate a USI engine into your Python code:

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# Use any USI engine
with SyncUsiEngine.from_config_path("engine.yaml") as engine:
    request = UsiThinkRequest(time_ms=5000)
    result = engine.think(sfen="startpos", request=request)
    print(f"Bestmove: {result.bestmove}, Score: {result.score_cp}cp")
```

Or with a direct path:

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine

with SyncUsiEngine.from_path("/path/to/engine") as engine:
    result = engine.think(sfen="startpos", request=UsiThinkRequest(nodes=1000000))
    print(result.bestmove)
```

### Example 2: Run Tournament via CLI

Use the provided example configuration:

```bash
# Tournament (Round-robin)
shogiarena run tournament examples/configs/run/tournament/example.yaml

# SPRT (Statistical testing)
shogiarena run sprt examples/configs/run/sprt/example.yaml

# SPSA (Parameter tuning)
shogiarena run spsa examples/configs/run/spsa/example.yaml
```

The dashboard will automatically open at `http://localhost:8080` to monitor progress.

**Note**: Example configs may use placeholders like `{output_dir}` and `{engine_dir}`. You can either run `shogiarena init` (alias for `shogiarena config init`) to set them up, or replace placeholders with absolute paths. See [Example Configurations](#example-configurations) for details.

## Core Features

### Tournament Modes

**Round-Robin Tournament**
```bash
shogiarena run tournament examples/configs/run/tournament/example.yaml
```
Run comprehensive engine comparisons with customizable time controls, opening positions, and adjudication rules.

**SPRT (Sequential Probability Ratio Test)**
```bash
shogiarena run sprt examples/configs/run/sprt/example.yaml
```
Efficiently test if one engine version is statistically stronger than another with early stopping.

**SPSA (Simultaneous Perturbation Stochastic Approximation)**
```bash
shogiarena run spsa examples/configs/run/spsa/example.yaml
```
Optimize engine parameters using gradient-based stochastic search with parallel game execution.

See [Tournament Guide](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments/) and [SPSA Guide](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa/) for detailed configuration options.

### Dashboard Features

- **Live Updates**: Real-time game progress and statistics
- **Interactive Visualizations**: Rating evolution, win-rate matrices, parameter convergence
- **Game Browser**: Replay and analyze individual games
- **SPSA Tracking**: Monitor parameter values and gradient estimates

### Python Library

Beyond simple engine interaction, the library provides advanced features:

**Asynchronous Engine Control**
```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def compare_engines():
    # Create multiple engines in parallel
    engine1 = await EngineFactory.create_engine("engine1.yaml")
    engine2 = await EngineFactory.create_engine("engine2.yaml")
    await asyncio.gather(engine1.start(), engine2.start())
    
    # Analyze same position concurrently
    results = await asyncio.gather(
        engine1.think(sfen="startpos", request=UsiThinkRequest(time_ms=5000)),
        engine2.think(sfen="startpos", request=UsiThinkRequest(time_ms=5000)),
    )
```

**Programmatic Tournament Execution**
```python
from shogiarena.arena.configs.tournament import ArenaConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner

# Run tournaments programmatically with full control
config = ArenaConfig.from_yaml("tournament.yaml")
runner = TournamentRunner(config)
runner.run_sync()  # or: await runner.run() for async
```

**Custom Analysis Tools**
```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# Build custom analysis workflows
with SyncUsiEngine.from_path("/path/to/engine") as engine:
    for position in my_position_list:
        result = engine.think(sfen=position, request=UsiThinkRequest(nodes=1000000))
        analyze_and_store(result)
```

See [Python Library Guide](https://nyoki-mtl.github.io/ShogiArena/user-guide/python-library/) for detailed API documentation.

## Documentation

- **[Getting Started](https://nyoki-mtl.github.io/ShogiArena/getting-started/)** - Installation and first steps
- **[User Guide](https://nyoki-mtl.github.io/ShogiArena/user-guide/)** - Tournament setup, SPSA tuning, Python API
- **[Technical Documentation](https://nyoki-mtl.github.io/ShogiArena/technical/)** - Architecture, USI protocol, services
- **[API Reference](https://nyoki-mtl.github.io/ShogiArena/api/)** - Core classes and modules
- **[Development Guide](DEVELOPMENT.md)** - Contributing, building, testing

## Example Configurations

The [`examples/configs/`](examples/configs/) directory provides ready-to-use configuration templates:

**Tournament Configurations:**
- [`examples/configs/run/tournament/example.yaml`](examples/configs/run/tournament/example.yaml) - Comprehensive tournament setup with detailed comments
- [`examples/configs/run/sprt/example.yaml`](examples/configs/run/sprt/example.yaml) - SPRT testing configuration
- [`examples/configs/run/spsa/example.yaml`](examples/configs/run/spsa/example.yaml) - SPSA parameter tuning setup

**Engine Configurations:**
- [`examples/configs/resources/engines/`](examples/configs/resources/engines/) - Engine configuration templates with various options

**Instance Configurations:**
- [`examples/configs/resources/instances/local_example.yaml`](examples/configs/resources/instances/local_example.yaml) - Local execution
- [`examples/configs/resources/instances/ssh_example.yaml`](examples/configs/resources/instances/ssh_example.yaml) - Remote SSH execution
- [`examples/configs/resources/instances/ssh_pool_example.yaml`](examples/configs/resources/instances/ssh_pool_example.yaml) - SSH instance pool

**Note**: Example configurations may use placeholders like `{output_dir}` and `{engine_dir}`. Run `shogiarena init` (alias for `shogiarena config init`) to configure these paths, or replace them with absolute paths.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
