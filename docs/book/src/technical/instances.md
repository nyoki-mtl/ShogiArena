# Instances 設計方針

instances は「どこで何局走らせるか」を扱う層です。設定モデル、runtime metrics、resource reservation、dashboard 表示がここでつながります。

## 基本構造

- static config
  `InstanceConfig`
- runtime state
  `Instance`, `InstanceMetrics`
- allocation boundary
  `InstancePool`
- remote execution support
  SSH transport / provisioner / runtime adapter

詳細な型は [Instances API](../api/instances.md) を参照してください。

## capacity の考え方

### `slots`

- `slots` は同時ジョブ数の上限です
- `null` は auto 扱いです
- auto のときは metrics の `cpu_count` を使います
- capacity が確定できない instance は安全側で予約しません

### `max_engines`

- 同時エンジンプロセス数の上限です
- 未指定なら slot capacity をベースに扱います
- `engine_processes` は実測値で、reservation ではありません

## resource reservation

resource の予約と解放は `Instance` 単体ではなく `InstancePool` 側でも扱います。

- `try_acquire_resources(...)`
- `release_resources(...)`

複数 instance にまたがる allocation をまとめて扱うことで、partial success によるリークを防ぎます。

## remote instance

### 前提

- SSH / bash 前提です
- `project_root` は remote 側 path として解釈します
- `engine_dir` は SSH instance では `{project_root}/data/engines` へ導出します

### 安全性

- host key checking は default で無効です（`strict_host_key_checking: false`）
- reachability が不明な instance には新規 job を出しません

## dashboard との関係

dashboard には instance 一覧、metrics、active game が流れます。`InstancePool.record_active_game()` などの bookkeeping は UI 表示と scheduler 可視化のために使われます。

## 設計上の注意

- capacity 不明は「割り当てない」
- finally での release を徹底する
- static config と runtime metrics を混ぜない

## 関連ファイル

- [`instance_config_models.py`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/instances/application/instance_config_models.py)
- [`instance_models.py`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/instances/application/instance_models.py)
- [`instance_pool.py`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/instances/application/instance_pool.py)
