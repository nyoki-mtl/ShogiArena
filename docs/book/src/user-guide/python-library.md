# Python Library Usage

ShogiArena は CLI ツールとしてだけでなく、Python ライブラリとしても使用できます。
エンジンとの対話、カスタムトーナメントの実行、データ分析など、柔軟なワークフローを構築できます。

**関連ドキュメント**:
- **[Engine API リファレンス](../api/engines.md)** - 詳細な API 仕様
- **[エンジンラッパーのレイヤー設計](../technical/engine-layers.md)** - 内部アーキテクチャ
- **[USI Engine の設計](../technical/usi-engine.md)** - 設計思想

## USI エンジンインターフェース

### SyncUsiEngine: 同期的な使用

`SyncUsiEngine` を使うと、`asyncio` を意識せずに USI エンジンを操作できます。

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# エンジン設定ファイルから起動
with SyncUsiEngine.from_config_path("configs/engine/yaneuraou.yaml") as engine:
    # 局面設定
    sfen = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
    
    # 思考 (5秒)
    request = UsiThinkRequest(movetime=5000)
    result = engine.think(sfen=sfen, request=request)
    
    print(f"Bestmove: {result.bestmove}")
    print(f"Score: {result.score_cp}")
    print(f"PV: {result.pv}")
```

#### 主なメソッド

- **`submit_position(sfen, moves=None)`**: 局面を設定
- **`think(sfen, request)`**: 思考を開始し、結果（`UsiThinkResult`）を返す
- **`think_mate(sfen, *, ply_limit=None, node_limit=None, infinite=False, wait_for_bestmove=None)`**: 詰将棋探索を実行
- **`analyze(sfen, request)`**: 無限解析を開始（`handle.stop()` で停止）
- **`new_game()`**: `usinewgame` を送信
- **`engine_info`**: エンジンの名前や作者情報を取得

#### 思考制御の例

```python
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# 固定時間での思考
request = UsiThinkRequest(movetime=3000)
result = engine.think(sfen="startpos", request=request)

# ノード数制限
request = UsiThinkRequest(nodes=1000000)
result = engine.think(sfen="startpos", request=request)

# フィッシャー時間制御（先手残り10秒、後手残り10秒、加算100ms）
request = UsiThinkRequest(btime=10000, wtime=10000, binc=100, winc=100)
result = engine.think(sfen="startpos", request=request)

# 秒読み
request = UsiThinkRequest(btime=60000, wtime=60000, byoyomi=10000)
result = engine.think(sfen="startpos", request=request)
```

#### 無限解析の使用

```python
# 無限解析を開始
request = UsiThinkRequest(infinite=True)
handle = engine.analyze(sfen="startpos", request=request)

# ... 何か他の処理 ...

# 解析を停止
handle.stop()
```

#### 詰将棋探索

```python
# 最大 31 手詰を探索
result = engine.think_mate(sfen="startpos", ply_limit=31)

if result.is_mate:
    print(f"Mate in {result.mate_in_ply} ply: {result.moves}")
else:
    print("No mate found")

# ノード制限
result = engine.think_mate(sfen="startpos", node_limit=200000)

# go mate infinite（bestmove を待って完了）
result = engine.think_mate(sfen="startpos", infinite=True, wait_for_bestmove=True, timeout=10.0)

# info 由来の最新 PV を参照
last_pv = result.get_last_pv()
if last_pv is not None:
    print(last_pv.nodes, last_pv.depth)
```

### AsyncUsiEngine: 非同期的な使用

並行処理や高度な制御が必要な場合は、`AsyncUsiEngine` を直接使用します。

```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def analyze_position():
    # エンジンを生成
    engine = await EngineFactory.create_engine("configs/engine/yaneuraou.yaml")
    await engine.start()
    
    try:
        # 思考実行
        request = UsiThinkRequest(movetime=5000)
        result = await engine.think(sfen="startpos", request=request)
        print(f"Bestmove: {result.bestmove}, Score: {result.score_cp}")
    finally:
        await engine.close()

# 実行
asyncio.run(analyze_position())
```

#### 複数エンジンの並行実行

```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def compare_engines():
    # 複数エンジンを並行起動
    engine1 = await EngineFactory.create_engine("engine1.yaml")
    engine2 = await EngineFactory.create_engine("engine2.yaml")
    
    await asyncio.gather(engine1.start(), engine2.start())
    
    try:
        # 同じ局面を並行解析
        request = UsiThinkRequest(movetime=5000)
        results = await asyncio.gather(
            engine1.think(sfen="startpos", request=request),
            engine2.think(sfen="startpos", request=request),
        )
        
        for i, result in enumerate(results, 1):
            print(f"Engine {i}: {result.bestmove} ({result.score_cp}cp)")
    finally:
        await asyncio.gather(engine1.close(), engine2.close())

asyncio.run(compare_engines())
```

### EngineFactory: エンジン生成

`EngineFactory` は設定ファイルから `AsyncUsiEngine` インスタンスを生成します。

```python
from shogiarena.arena.engines.engine_factory import EngineFactory

# 設定ファイルから生成
engine = await EngineFactory.create_engine("configs/engine/yaneuraou.yaml")

# 直接パスを指定
engine = await EngineFactory.create_engine("/path/to/engine/binary")
```

## トーナメント Runner

Runner クラスを使用すると、プログラムからトーナメントを実行できます。

### TournamentRunner: ラウンドロビントーナメント

```python
from pathlib import Path

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import FilesystemRunStorage

# 設定ファイルから読み込み
config = TournamentRunConfig.from_yaml("configs/arena/tournament.yaml")

# トーナメントを実行
storage = FilesystemRunStorage(Path("runs/tournament"))
runner = TournamentRunner(config, storage=storage)
runner.run_sync()  # 完了まで待機

# または非同期で実行
import asyncio
asyncio.run(runner.run())
```

#### カスタム設定の作成

```python
from shogiarena.arena.configs.tournament import (
    TournamentRunConfig,
    TournamentConfig,
    RulesConfig,
    TimeControlConfig,
)

config = TournamentRunConfig(
    experiment_name="custom_tournament",
    engines=[
        {"engine_path": "engine1.yaml"},
        {"engine_path": "engine2.yaml"},
    ],
    tournament=TournamentConfig(
        scheduler="round_robin",
        games_per_pair=100,
        num_parallel=4,
    ),
    rules=RulesConfig(
        time_control=TimeControlConfig(
            time_ms=30000,
            increment_ms=300,
        ),
    ),
)

storage = FilesystemRunStorage(Path("runs/custom-tournament"))
runner = TournamentRunner(config, storage=storage)
runner.run_sync()
```

### SprtRunner: SPRT テスト

SPRT (Sequential Probability Ratio Test) を使用して、統計的に有意な強さの差を検出します。

```python
from pathlib import Path

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.sprt_runner import SprtRunner
from shogiarena.arena.storage import FilesystemRunStorage

# SPRT 設定を含むトーナメント設定
config = TournamentRunConfig.from_yaml("configs/arena/sprt.yaml")

# SPRT テストを実行
storage = FilesystemRunStorage(Path("runs/sprt"))
runner = SprtRunner(config, storage=storage)
runner.run_sync()

# テスト結果は自動的に早期停止（H0 棄却または受容）
```

**SPRT 設定例**:
```yaml
experiment_name: "sprt_test"

engines:
  - engine_path: "baseline.yaml"
  - engine_path: "modified.yaml"

tournament:
  scheduler: round_robin
  num_parallel: 8

rules:
  time_control:
    time_ms: 10000
    increment_ms: 100

sprt:
  enabled: true
  elo0: 0      # H0: Elo差が 0 以下
  elo1: 5      # H1: Elo差が 5 以上
  alpha: 0.05  # 第一種過誤率
  beta: 0.05   # 第二種過誤率
```

### SpsaRunner: パラメータチューニング

SPSA (Simultaneous Perturbation Stochastic Approximation) を使用して、エンジンパラメータを最適化します。

```python
from pathlib import Path

from shogiarena.arena.configs.spsa import load_config_yaml
from shogiarena.arena.runners.spsa_runner import SpsaRunner
from shogiarena.arena.storage import FilesystemRunStorage

# SPSA 設定を読み込み
config = load_config_yaml("configs/spsa/tune_params.yaml")

# チューニング実行
storage = FilesystemRunStorage(Path("runs/spsa"))
runner = SpsaRunner(config, storage=storage)
runner.run_sync()

# 最適なパラメータは自動的に記録される
```

**SPSA 設定例**:
```yaml
experiment_name: "spsa_tuning"

base_engine: "baseline.yaml"

parameters:
  - name: "Contempt"
    min: -100
    max: 100
    start: 0
    c: 10
    a_ratio: 0.1
  
  - name: "Selectivity"
    min: 1
    max: 10
    start: 5
    c: 1
    a_ratio: 0.05

spsa:
  iterations: 100
  games_per_iteration: 20
  num_parallel: 4

rules:
  time_control:
    time_ms: 5000
    increment_ms: 50
```

## データクラスと型

### UsiThinkRequest

思考条件を指定するデータクラス。

```python
from dataclasses import dataclass

@dataclass
class UsiThinkRequest:
    movetime: int | None = None         # 固定時間（ミリ秒）
    btime: int | None = None            # 先手の残り時間（ミリ秒）
    wtime: int | None = None            # 後手の残り時間（ミリ秒）
    binc: int | None = None             # 先手のインクリメント（ミリ秒）
    winc: int | None = None             # 後手のインクリメント（ミリ秒）
    byoyomi: int | None = None          # 秒読み（ミリ秒）
    depth: int | None = None            # 探索深さ制限
    nodes: int | None = None            # ノード数制限
    infinite: bool = False              # 無限解析モード
    ponder: bool = False                # Ponder モード
    searchmoves: tuple[str, ...] = ()   # 探索する手を制限
```

### UsiThinkResult

思考結果を保持するデータクラス。

```python
@dataclass
class UsiThinkResult:
    bestmove: str                       # 最善手（USI形式）
    ponder: str | None = None           # 予想手
    score_cp: int | None = None         # 評価値（センチポーン）
    score_mate: int | None = None       # 詰み手数
    pv: list[str] = field(default_factory=list)  # 読み筋
```

**使用例**:
```python
result = engine.think(sfen="startpos", request=UsiThinkRequest(movetime=5000))

print(f"Best move: {result.bestmove}")

if result.score_cp is not None:
    print(f"Evaluation: {result.score_cp / 100:.2f} pawns")

if result.score_mate is not None:
    print(f"Mate in {result.score_mate} moves")

if result.pv:
    print(f"Principal variation: {' '.join(result.pv)}")
```

### UsiMateResult

詰将棋探索の結果を保持するデータクラス。

```python
@dataclass
class UsiMateResult:
    is_mate: bool                       # 詰みが見つかったか
    moves: tuple[str, ...] = ()         # 詰み手順
    mate_in_ply: int | None = None      # 詰み手数（ply）
```

## 実用例

### 大規模なエンジン比較

```python
from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner

# 複数エンジンを総当たりで比較
config = TournamentRunConfig(
    experiment_name="engine_comparison",
    engines=[
        {"engine_path": f"engine_{i}.yaml"}
        for i in range(1, 6)  # 5つのエンジン
    ],
    tournament=TournamentConfig(
        scheduler="round_robin",
        games_per_pair=50,
        num_parallel=10,
    ),
    rules=RulesConfig(
        time_control=TimeControlConfig(
            time_ms=60000,
            increment_ms=1000,
        ),
    ),
)

storage = FilesystemRunStorage(Path("runs/engine_comparison"))
runner = TournamentRunner(config, storage=storage)
runner.run_sync()

# 結果はデータベースとダッシュボードで確認可能
```

### カスタム解析ツール

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

def analyze_game(kifu_path: str, engine_path: str):
    """棋譜全体をエンジンで解析"""
    # 棋譜読み込み（rshogi などを使用）
    positions = load_kifu(kifu_path)
    
    with SyncUsiEngine.from_config_path(engine_path) as engine:
        results = []
        
        for i, sfen in enumerate(positions):
            request = UsiThinkRequest(movetime=3000)
            result = engine.think(sfen=sfen, request=request)
            results.append({
                "move": i + 1,
                "bestmove": result.bestmove,
                "score": result.score_cp,
            })
            print(f"Move {i+1}: {result.bestmove} ({result.score_cp}cp)")
        
        return results

# 使用例
results = analyze_game("game.kif", "strong_engine.yaml")
```

### バッチ処理

```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def batch_analyze(positions: list[str], engine_path: str):
    """複数局面を並行解析"""
    engine = await EngineFactory.create_engine(engine_path)
    await engine.start()
    
    try:
        request = UsiThinkRequest(movetime=5000)
        tasks = [
            engine.think(sfen=sfen, request=request)
            for sfen in positions
        ]
        results = await asyncio.gather(*tasks)
        return results
    finally:
        await engine.close()

# 使用例
positions = [
    "startpos",
    "sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
    # ... more positions
]
results = asyncio.run(batch_analyze(positions, "engine.yaml"))
```

## 参考資料

### ユーザーガイド

- [トーナメントガイド](tournaments.md): トーナメント設定の詳細
- [SPSA ガイド](spsa.md): パラメータチューニングの詳細
- [エンジン設定](engine-configuration.md): エンジン設定ファイルの書き方
- [設定システム](configuration.md): 環境設定とプレースホルダー

### 技術ドキュメント

- [USI プロトコル](../technical/usi-engine.md): USI エンジンの技術詳細
- [アーキテクチャ](../technical/architecture.md): システム全体の設計
- [API リファレンス](../api/index.md): 全クラスとメソッドのリファレンス
