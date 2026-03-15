# Instances API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。instance 管理は現状 public facade に出していません。外部利用者に保証する API ではなく、runner / dashboard / remote execution の内部部品として扱います。

instances レイヤーは、ローカル実行と SSH リモート実行の配置情報と実行容量を扱います。トーナメント orchestration が「どの instance で何局動かせるか」を判断する基礎です。

## 主な型

```python
from shogiarena._core.contexts.instances.application.instance_config_models import (
    InstanceConfig,
    InstancesConfig,
    InstanceType,
)
from shogiarena._core.contexts.instances.application.instance_models import (
    Instance,
    InstanceMetrics,
)
from shogiarena._core.contexts.instances.application.instance_pool import (
    InstancePool,
)
```

## `InstanceType`

- `LOCAL`
- `SSH`

型の違いは主に engine 配置先、resource capacity の見積もり、SSH RTT probe の有無に反映されます。

## `InstanceConfig`

[`InstanceConfig`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/instances/application/instance_config_models.py) は YAML 由来の静的設定です。

主要フィールド:

- `name`
- `type`
- `engine_dir`
- `project_root`
- `host`
- `user`
- `port`
- `identity_file`
- `slots`
- `max_engines`
- `tags`
- `is_strict_host_key_checking`
- `should_install_requirements`

重要な正規化:

- `LOCAL` では `engine_dir` を指定できません
- `SSH` では `project_root` から `engine_dir = {project_root}/data/engines` を導出します
- `slots = null` は auto 扱いです

## `InstanceMetrics`

[`InstanceMetrics`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/instances/application/instance_models.py) は runtime 観測値です。

代表例:

- 到達可能性 `is_reachable`
- CPU / memory 使用量
- `in_use_slots`, `in_use_engines`
- `engine_processes`
- `latency_*`
- `network_rtt_*`

`Instance` はこの metrics と static config を組み合わせて有効 capacity を計算します。

## `Instance`

runtime 上の 1 instance を表します。

主要 property:

- `name`
- `type`
- `is_local`
- `is_ssh`
- `effective_slots`
- `available_slots`
- `max_engine_capacity`
- `available_engines`
- `can_accept_job`

主要メソッド:

- `try_acquire_resources(slots=0, engines=0) -> bool`
- `release_resources(slots=0, engines=0) -> None`
- `update_metrics(new_metrics) -> None`
- `add_engine_processes(delta=1) -> None`
- `remove_engine_processes(delta=1) -> None`
- `start_network_probe(...)`
- `stop_network_probe(...)`

## `InstancePool`

[`InstancePool`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/instances/application/instance_pool.py) は thread-safe な instance 集約です。

### 読み込み

- `load_from_yaml(yaml_path)`
- `load_default_local()`
- `ensure_local_instance()`

`load_from_yaml()` は shorthand も吸収します。

- 単一 entry
- `hosts:` 展開
- default `name` 補完

### 運用 API

- `get_instance(name)`
- `list_instances()`
- `add_instance(config)`
- `update_instance_config(name, new_config)`
- `remove_instance(name)`
- `set_drain(name, is_drain_enabled)`

### resource 予約

- `try_acquire_resources(requirements)`
- `release_resources(allocations)`

複数 instance にまたがる resource reservation は pool 側でまとめて扱います。

### dashboard 用の付加情報

- `record_active_game(...)`
- active game tracking
- drain state
- source path

## 設定ファイルについて

instances YAML は現在 canonical に `{"instances": [...]}` を書く形式ではなく、1 entry または `hosts:` 展開を前提に正規化しています。`InstancePool._normalize_config_data()` が最終的に canonical 形式へ変換します。

このため contributor は「docs 上の見た目の YAML」と「内部 canonical object」は一致しないことがあります。

## 使用例

```python
from pathlib import Path

from shogiarena._core.contexts.instances.application.instance_pool import (
    InstancePool,
)

pool = InstancePool.load_from_yaml(Path(".sandbox/configs/instances/local.yaml"))
local = pool.ensure_local_instance()

if local.try_acquire_resources(slots=2, engines=2):
    try:
        print(local.available_slots)
    finally:
        local.release_resources(slots=2, engines=2)
```

## 設計メモ

- static config と runtime metrics は分離しています
- scheduler の resource reservation は `InstancePool` 境界へ寄せています
- public API にしないのは、remote execution の責務分割がまだ変化しやすいためです
