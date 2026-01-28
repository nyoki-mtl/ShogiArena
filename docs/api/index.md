# API リファレンス

Shogi Arena を Python ライブラリとして使用するための API ドキュメントです。

## API 概要

Shogi Arena は以下の主要な API を提供しています：

### Core API

基本的なデータ構造とコアクラス：

- **ArenaConfig**: トーナメント設定
- **EngineSpec**: エンジン仕様
- **GameResult**: 対局結果
- **TimeControlLimits**: 持ち時間設定

[詳細はこちら](core.md)

### Engine API

USI エンジンとの通信：

- **AsyncUsiEngine**: 非同期 USI エンジン
- **SyncUsiEngine**: 同期的な USI エンジンラッパー
- **EngineFactory**: エンジンの生成と初期化
- **UsiThinkRequest**: 思考リクエスト
- **UsiThinkResult**: 思考結果

[詳細はこちら](engines.md)

### Services API

横断的な機能：

- **RatingService**: Elo レーティング計算
- **SprtService**: 統計的仮説検定
- **StatisticsService**: 対局統計
- **DatabaseService**: データ永続化
- **AdjudicationService**: 投了・引き分け判定

[詳細はこちら](services.md)

## インストール

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync --all-extras
```

## 基本的な使い方

### SyncUsiEngine で簡単にエンジンを操作

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# エンジンを起動
with SyncUsiEngine.from_config_path("configs/engine/yaneuraou.yaml") as engine:
    # 思考させる
    request = UsiThinkRequest(time_ms=5000)
    result = engine.think(
        sfen="lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
        request=request
    )
    print(f"最善手: {result.bestmove}")
```

詳細は [Python Library](../user-guide/python-library.md) を参照。

### AsyncUsiEngine で非同期処理

```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def main():
    # エンジンを作成
    engine = await EngineFactory.create_engine("configs/engine/yaneuraou.yaml")
    await engine.start()

    # 思考
    request = UsiThinkRequest(time_ms=5000)
    result = await engine.think(
        sfen="lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
        request=request
    )
    print(f"最善手: {result.bestmove}")

    await engine.close()

asyncio.run(main())
```

### レーティング計算

```python
from shogiarena.arena.services.rating import RatingService

# レーティングサービスを初期化
service = RatingService(initial_rating=1500, k_factor=16)

# 対局結果を処理
new_ratings = service.update_ratings(
    engine1="EngineA",
    engine2="EngineB",
    result="win"  # EngineA が勝利
)

print(f"EngineA: {new_ratings['EngineA']:.1f}")
print(f"EngineB: {new_ratings['EngineB']:.1f}")
```

### SPRT による統計的検定

```python
from shogiarena.arena.services.sprt import SprtService

# SPRT サービスを初期化
service = SprtService(
    elo0=0,      # 帰無仮説: 差がない
    elo1=10,     # 対立仮説: 10 Elo の差
    alpha=0.05,
    beta=0.05
)

# 対局結果から LLR を計算
llr = service.calculate_llr(wins=55, losses=45, draws=10)
decision = service.check_decision(llr)

if decision == "H1":
    print("統計的に有意な差が検出されました")
elif decision == "H0":
    print("有意な差は検出されませんでした")
else:
    print("継続が必要です")
```

## パッケージ構造

```
shogiarena/
├── arena/
│   ├── configs/          # 設定関連
│   │   ├── tournament.py # トーナメント設定
│   │   └── base.py       # 基本設定
│   ├── engines/          # エンジン層
│   │   ├── usi_engine.py       # AsyncUsiEngine
│   │   ├── sync_usi_engine.py  # SyncUsiEngine
│   │   ├── engine_factory.py   # EngineFactory
│   │   └── usi_think.py        # UsiThinkRequest/Result
│   ├── orchestrators/    # オーケストレーター
│   ├── runners/          # Runner
│   ├── services/         # サービス層
│   │   ├── rating.py     # RatingService
│   │   ├── sprt.py       # SprtService
│   │   └── statistics.py # StatisticsService
│   └── instances/        # インスタンス管理
└── web/
    └── dashboard/        # ダッシュボード
```

## 型ヒント

Shogi Arena はすべての公開 API に型ヒントを提供しています。

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest, UsiThinkResult

def analyze_position(engine: SyncUsiEngine, sfen: str) -> UsiThinkResult:
    """局面を分析して最善手を返す"""
    request = UsiThinkRequest(depth_limit=20)
    return engine.think(sfen=sfen, request=request)
```

mypy による型チェック：

```bash
mypy your_script.py
```

## エラーハンドリング

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine

try:
    engine = SyncUsiEngine.from_config_path("config.yaml", timeout=10.0)
    engine.start()
    # ... エンジンを使用 ...
except FileNotFoundError:
    print("設定ファイルが見つかりません")
except TimeoutError:
    print("エンジンの起動がタイムアウトしました")
except RuntimeError as e:
    print(f"エンジンエラー: {e}")
finally:
    if engine:
        engine.close()
```

## 非同期コンテキストマネージャー

```python
from shogiarena.arena.engines.engine_factory import EngineFactory

async def run_analysis():
    async with await EngineFactory.create_engine("config.yaml") as engine:
        await engine.start()
        result = await engine.think(sfen=sfen, request=request)
        return result
    # 自動的に engine.close() が呼ばれる
```

## 次のステップ

詳細な API ドキュメントは以下を参照してください：

- **[Core API](core.md)** - 基本的なデータ構造
- **[Engine API](engines.md)** - エンジン操作
- **[Services API](services.md)** - サービス層

実践的な使い方は以下を参照：

- **[Python Library](../user-guide/python-library.md)** - 実用的な例
- **[Technical Details](../technical/architecture.md)** - 内部実装の詳細
