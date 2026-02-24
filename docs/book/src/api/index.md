# API Reference

Shogi Arena を Python ライブラリとして使用するための API リファレンスです。

## API 一覧

| カテゴリ | 説明 | ドキュメント |
|---------|------|-------------|
| **CLI** | コマンドラインインターフェース | [cli.md](cli.md) |
| **Configs** | 設定モデル（トーナメント・SPSA） | [configs.md](configs.md) |
| **Engines** | USI エンジン操作 | [engines.md](engines.md) |
| **Runners** | トーナメント/SPSA 実行 | [runners.md](runners.md) |
| **Orchestrators** | 並列実行制御 | [orchestrators.md](orchestrators.md) |
| **Scheduler** | 対局スケジューリング | [scheduler.md](scheduler.md) |
| **Execution** | 対局実行 | [execution.md](execution.md) |
| **Services** | 統計・永続化・判定 | [services.md](services.md) |
| **Records** | 棋譜データ・パーサ | [records.md](records.md) |
| **Instances** | インスタンス管理 | [instances.md](instances.md) |
| **Session** | セッション制御 | [session.md](session.md) |
| **Storage** | ストレージ抽象 | [storage.md](storage.md) |
| **Dashboard** | Web ダッシュボード | [dashboard.md](dashboard.md) |
| **DB** | データベース | [db.md](db.md) |
| **Utils** | ユーティリティ | [utils.md](utils.md) |

## インストール

```bash
pip install shogiarena
```

開発版をインストールする場合:

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync --all-extras
```

## クイックスタート

### エンジンで思考させる（同期版）

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

with SyncUsiEngine.from_config_path("configs/engine/yaneuraou.yaml") as engine:
    result = engine.think(sfen="startpos", request=UsiThinkRequest(byoyomi=1000))
    print(result.bestmove)
```

### エンジンで思考させる（非同期版）

```python
import asyncio
from pathlib import Path

from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def main():
    engine = await EngineFactory.create_engine(Path("configs/engine/yaneuraou.yaml"))
    await engine.start()
    result = await engine.think(sfen="startpos", request=UsiThinkRequest(byoyomi=1000))
    print(result.bestmove)
    await engine.close()

asyncio.run(main())
```

### トーナメントを実行する

```python
from pathlib import Path

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import FilesystemRunStorage

config = TournamentRunConfig.from_yaml("configs/run/arena/tournament.yaml")
storage = FilesystemRunStorage(Path("runs/tournament"))
runner = TournamentRunner(config, storage=storage)
result = runner.run_sync()
print(result.tournament.get_leaderboard())
```

## 次のステップ

- [Python ライブラリガイド](../user-guide/python-library.md) - API を組み合わせた実践例
- [アーキテクチャ](../technical/architecture.md) - 内部構造の理解
- [Runners & Orchestrators](../technical/runners-orchestrators.md) - 実行フローの詳細
