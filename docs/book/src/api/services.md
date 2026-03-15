# Services API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。ここで扱う統計サービスは public facade に出していません。library 利用者向けの安定 API としては保証しない前提です。

このページでは、runner や dashboard summary が内部で使う統計系サービスをまとめます。対象は主に Elo、SPRT、BTD 推定、pentanomial 集計です。

## 主な型

```python
from shogiarena._core.contexts.game_session.application.elo_rating_service import (
    EloRatingService,
)
from shogiarena._core.contexts.game_session.application.sprt_service import (
    Sprt,
    SprtDecision,
    SprtResult,
)
from shogiarena._core.shared.kernel.statistics.btd_estimation.estimator import (
    BTDEstimator,
)
from shogiarena._core.shared.kernel.statistics.pentanomial import (
    compute_pentanomial,
)
```

## `EloRatingService`

[`EloRatingService`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/application/elo_rating_service.py) は最小の Elo 更新サービスです。

- `initial_rating`
- `k_factor`
- `_current_ratings`

現在の主メソッドは `update_ratings(black_player, white_player, game_result)` だけです。ゲーム結果に応じて内部キャッシュを更新し、新しい先手/後手 rating を返します。

このサービスは「履歴分析 API」ではなく、incremental な rating 更新器として置かれています。

## `Sprt`

[`Sprt`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/application/sprt_service.py) は逐次確率比検定の状態機械です。

主要要素:

- `elo0`, `elo1`
- `alpha`, `beta`
- `lower_bound`, `upper_bound`
- `wins`, `draws`, `losses`
- `games_played`
- `llr`

主要メソッド:

- `add_game_result(result) -> SprtResult`
- `get_status() -> SprtResult`
- `to_snapshot() -> SprtStateSnapshot`
- `from_snapshot(snapshot) -> Sprt`
- `reset()`
- `is_finished()`

### `SprtDecision`

- `CONTINUE`
- `ACCEPT_H0`
- `ACCEPT_H1`

### `SprtResult`

判定時点の snapshot です。

- `llr`
- `lower_bound`
- `upper_bound`
- `decision`
- `games_played`
- `wins`, `draws`, `losses`
- `win_rate`
- `elo_estimate`

## `BTDEstimator`

[`BTDEstimator`](/workspaces/ShogiArena/src/shogiarena/_core/shared/kernel/statistics/btd_estimation/estimator.py) は Bradley-Terry-Davidson モデルで rating を推定します。

入口は `estimate(games, anchor_name=None, engine_names=None)` です。

返り値の `BTDEstimate` には次が含まれます。

- engine ごとの rating
- 標準誤差
- 共分散
- anchor
- color advantage (`gamma_elo`)
- draw tendency (`nu`, `draw_eq`)

主に最終 summary や分析表示用です。

## `compute_pentanomial()`

[`compute_pentanomial()`](/workspaces/ShogiArena/src/shogiarena/_core/shared/kernel/statistics/pentanomial.py) は paired game を pentanomial bin に集約します。

返り値:

- `pairs`
- `bins`
  `{"2.0", "1.5", "1.0", "0.5", "0.0"}`

同じ engine pair と初期局面ごとに grouped し、片側先手と片側後手の 2 局を 1 サンプルとして数えます。

## 使用例

```python
from shogiarena._core.contexts.game_session.application.sprt_service import Sprt
from shogiarena._core.shared.kernel.game_results import GameResult

sprt = Sprt(elo0=0.0, elo1=5.0, alpha=0.05, beta=0.05)
status = sprt.add_game_result(GameResult.WHITE_WIN)

print(status.decision, status.llr)
```

## 設計メモ

- これらは「統計計算の部品」であり、公開 library API ではありません
- facade 化するなら高水準の analysis API を別に設計した方がよく、内部 service をそのまま公開する段階ではありません
