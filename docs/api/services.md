# Services API

Shogi Arena の各コンポーネントが利用する共通サービスです。

## RatingService

Elo レーティング計算を行います。

```python
from shogiarena.arena.services.rating import RatingService

service = RatingService(initial_rating=1500, k_factor=16)
```

### メソッド

#### `update_ratings(engine1, engine2, result) -> dict[str, float]`
対局結果に基づいてレーティングを更新し、新しい値を返します。

## SprtService

SPRT (Sequential Probability Ratio Test) による統計的検定を行います。

```python
from shogiarena.arena.services.sprt import SprtService

service = SprtService(elo0=0, elo1=10, alpha=0.05, beta=0.05)
```

### メソッド

#### `calculate_llr(wins, losses, draws) -> float`
Log Likelihood Ratio を計算します。

#### `check_decision(llr) -> str`
判定結果 (`"H0"`, `"H1"`, `"continue"`) を返します。

## DatabaseService

SQLite データベースへの対局結果の保存と読み込みを担当します。

```python
from shogiarena.arena.services.persistence.database import DatabaseService
```

## AdjudicationService

対局中の投了・引き分け判定ロジックを提供します。

```python
from shogiarena.arena.services.game_control.adjudication import AdjudicationService
```