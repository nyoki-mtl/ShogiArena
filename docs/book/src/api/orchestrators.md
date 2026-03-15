# Orchestrators API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。利用者向けには `shogiarena.tournament.run_tournament()` や `TournamentRunner` を入口にしたいので、orchestrator 自体は public facade に出していません。

orchestrator は runner が作った work item を並列実行する層です。責務は scheduling そのものではなく、engine pool、instance pool、progress、completion hooks を束ねて「実際に走らせる」ことです。

## 主な型

```python
from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import (
    BaseOrchestrator,
)
from shogiarena._core.contexts.tournament.adapters.orchestrator import (
    TournamentOrchestrator,
)
from shogiarena._core.contexts.spsa.adapters.orchestrator import (
    SpsaOrchestrator,
)
```

## `BaseOrchestrator`

[`BaseOrchestrator`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/adapters/orchestration/contracts_base_orchestrator.py) は tournament / SPSA 共通の実行基盤です。

主な責務:

- `EnginePool` の生成
- `GameRunner` の生成
- progress consumer の起動
- engine option / info snapshot の集約
- completion hook の発火
- 協調停止と shutdown

主要メソッド:

- `create_engine_pool(max_instances_per_engine)`
- `init_engine_pool_for_roles(num_workers, name_a, name_b)`
- `get_engine_option_snapshots()`
- `get_engine_info_snapshots()`
- `request_stop()`
- `shutdown()`
- `start_progress_consumer(...)`

## `TournamentOrchestrator`

[`TournamentOrchestrator`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/tournament/adapters/orchestrator.py) は `GameSpec` の列を並列実行する orchestrator です。

流れ:

1. runner から schedule を受け取る
2. pending game を収集する
3. local / remote の実行モードを判断する
4. game assignment と worker preassignment を行う
5. 完了時に hook と DB 保存を進める

主要メソッド:

- `set_work_items(schedule, completed)`
- `run()`
- `enqueue_restored_game(spec, display_order)`

補助 service は `game_execution_service`、`selection_service`、`completion_emission_service` などに分割されています。

## `SpsaOrchestrator`

[`SpsaOrchestrator`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/spsa/adapters/orchestrator.py) は update batch を単位に SPSA 対局を回します。

特徴:

- baseline / tuned の role-aware engine pool
- parameter update lock
- optional LTC regression
- mixin による責務分割

runner から update items と parameter 群を受け取って実行します。

## session / hooks との関係

orchestrator は次の内部境界に依存します。

- `SessionContext`
- `GameLifecycleHooks`
- `DatabaseServicePort`
- `EngineFactoryService`
- `InstancePool`

つまり、runner が runtime wiring を済ませたあとで orchestrator を起動する構図です。

## dashboard との関係

orchestrator は直接 dashboard 実装に依存せず、progress hub や server port を介して worker snapshot と summary update を流します。schedule の dashboard 操作は別の [`DashboardScheduleFacade`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/tournament/adapters/dashboard_schedule_facade.py) に分かれています。

## 設計メモ

- runner は「実験全体の制御」
- orchestrator は「並列 execution の制御」
- `GameRunner` は「1 局の execution」

この分離を保つため、orchestrator をそのまま public API にしない方針です。
