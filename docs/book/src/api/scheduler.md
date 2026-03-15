# Scheduler API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。scheduler は public facade に出していません。外部利用者に見せたいのは `run_tournament()` や `TournamentRunner` であり、個々の scheduler 実装は内部部品です。

tournament scheduler は `GameSpec` の列を生成します。責務は「どの engine 同士を、どの初期局面で、どの順番で対局させるか」を決めることです。

## 主な型

```python
from shogiarena._core.contexts.tournament.application.schedule_generation import (
    GameScheduler,
    GauntletScheduler,
    RoundRobinScheduler,
    SelfPlayScheduler,
    create_scheduler,
)
from shogiarena._core.contexts.tournament.domain.tournament_models import (
    GameSpec,
)
```

## `GameSpec`

[`GameSpec`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/tournament/domain/tournament_models.py) は 1 局の scheduling 単位です。

主要フィールド:

- `black_engine`
- `white_engine`
- `initial_sfen`
- `game_id`
- `round_num`
- `assigned_instance_black`
- `assigned_instance_white`
- `should_require_install`

`GameSpec.create(black, white, sfen, round_num, seed)` は stable な `game_id` を生成します。

## `GameScheduler`

abstract base class です。

- `generate_schedule(engines, games_per_pair, seed, initial_positions) -> list[GameSpec]`
- `get_total_games(num_engines, games_per_pair) -> int`

`engines` 側に求めるのは厳密な concrete type ではなく、少なくとも `name` を持つことです。

## `SelfPlayScheduler`

1 engine だけで自己対局を生成します。

特徴:

- engine は 1 台ちょうど必要
- `flip_policy == "pair_both"` では同一局面を 2 回使います
- 総局数は `games_per_pair`

## `RoundRobinScheduler`

総当たりを生成します。

特徴:

- 2 engine 以上必要
- `alternate` / `random` / `pair_both` の先後 policy を扱います
- 総局数は `n * (n - 1) / 2 * games_per_pair`

## `GauntletScheduler`

先頭 `baseline_count` 台を baseline とし、残り challenger とだけ当てます。

特徴:

- `baseline_count >= 1`
- baseline と challenger の直積だけを schedule します
- `pair_both` では同一局面で先後を入れ替えます

## `create_scheduler()`

factory は現在次の 3 種だけを受け付けます。

- `"selfplay"`
- `"round_robin"`
- `"gauntlet"`

`"swiss"` は現行実装にはありません。以前のドキュメントにあった記述は古いものです。

## 初期局面 source

scheduler が依存するのは `InitialPositionSource` protocol です。

- `flip_policy: str`
- `generate(count: int, seed: str) -> list[str]`

つまり scheduler 自体は opening book や file format を知りません。必要数の SFEN を供給する source に依存します。

## 使用例

```python
from shogiarena.tournament import load_tournament_config
from shogiarena._core.contexts.tournament.application.schedule_generation import (
    create_scheduler,
)

config = load_tournament_config(".sandbox/configs/tournament/example.yaml")
scheduler = create_scheduler(config.tournament.scheduler)

schedule = scheduler.generate_schedule(
    engines=config.engines,
    games_per_pair=config.tournament.games_per_pair,
    seed=config.tournament.seed,
    initial_positions=config.rules.initial_positions,
)
```

## 設計メモ

- scheduler は pairing のみを担当し、実行状態や停止判定は持ちません
- `GameSpec` 生成は deterministic id を含み、resume と dashboard 参照の基礎になります
- public facade にしないのは、config model と scheduler protocol の境界がまだ内部都合で変化しやすいためです
