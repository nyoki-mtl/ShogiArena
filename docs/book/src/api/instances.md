# Instances API

インスタンス管理の API リファレンスです。ローカル/SSH インスタンスのモデル、リソースプール、スロット見積もりを提供します。

## 概要

Instances API は対局を実行するインスタンス（ローカルまたはリモート）を管理します。

- **Instance**: インスタンスの状態を表すモデル
- **InstancePool**: インスタンスのプール管理
- **InstanceConfig**: インスタンスの設定

---

## InstanceType 列挙型

インスタンスの種類を表す列挙型です。

```python
from shogiarena.arena.instances.models import InstanceType
```

- **LOCAL**: `"local"` ローカルインスタンス
- **SSH**: `"ssh"` SSH 経由のリモートインスタンス

---

## InstanceConfig クラス

インスタンスの設定を表すデータクラスです。

```python
from shogiarena.arena.instances.models import InstanceConfig
```

```python
InstanceConfig(
    name: str,
    type: InstanceType,
    engine_dir: str,
    ...
)
```

### 共通フィールド

- **name**: `str` インスタンス名（英数字、ハイフン、アンダースコア、ドットのみ）
- **type**: `InstanceType` インスタンス種別
- **engine_dir**: `str` エンジンディレクトリ（SSH の場合は `project_root` から自動導出。LOCAL の場合は空文字列）
- **slots**: `int | None` (デフォルト: `None`) 利用可能スロット数（`None` で自動検出）
- **max_engines**: `int | None` (デフォルト: `None`) 最大エンジン数
- **tags**: `list[str]` (デフォルト: `[]`) タグ

### SSH 固有フィールド

- **host**: `str | None` (デフォルト: `None`) ホスト名
- **user**: `str | None` (デフォルト: `None`) ユーザー名
- **port**: `int` (デフォルト: `22`) ポート番号
- **identity_file**: `str | None` (デフォルト: `None`) 秘密鍵ファイルパス
- **project_root**: `str` (デフォルト: `""`) リモートのプロジェクトルート（SSH の場合、未指定時は `$HOME/ShogiArena-remote`）
- **strict_host_key_checking**: `bool` (デフォルト: `True`) ホスト鍵の厳格なチェック
- **install_requirements**: `bool` (デフォルト: `False`) リモートに要件をインストールするか

---

## InstanceMetrics クラス

インスタンスのランタイムメトリクスを表すデータクラスです。

```python
from shogiarena.arena.instances.models import InstanceMetrics
```

### フィールド

- **timestamp**: `float` メトリクス取得時刻
- **reachable**: `bool` (デフォルト: `True`) 到達可能か
- **load_avg_1**: `float | None` 1分間ロードアベレージ
- **load_avg_5**: `float | None` 5分間ロードアベレージ
- **load_avg_15**: `float | None` 15分間ロードアベレージ
- **cpu_usage_pct**: `float | None` CPU 使用率
- **cpu_model**: `str | None` CPU モデル名
- **cpu_count**: `int | None` CPU コア数
- **cpu_usage_pct_per_core**: `list[float] | None` コアごとの CPU 使用率
- **mem_total_mb**: `int | None` 総メモリ（MB）
- **mem_free_mb**: `int | None` 空きメモリ（MB）
- **mem_used_mb**: `int | None` 使用メモリ（MB）
- **mem_used_pct**: `float | None` メモリ使用率
- **in_use_slots**: `int` (デフォルト: `0`) 使用中スロット数
- **in_use_engines**: `int` (デフォルト: `0`) 使用中エンジン数
- **engine_processes**: `int` (デフォルト: `0`) エンジンプロセス数
- **latency_avg_ms**: `float | None` レイテンシ平均（ミリ秒）
- **latency_recent_ms**: `float | None` 最新レイテンシ（ミリ秒）
- **latency_samples**: `int` (デフォルト: `0`) レイテンシサンプル数
- **latency_alert**: `bool` (デフォルト: `False`) レイテンシアラート
- **latency_threshold_ms**: `int | None` レイテンシ閾値（ミリ秒）
- **network_rtt_avg_ms**: `float | None` ネットワーク RTT 平均（ミリ秒）
- **network_rtt_recent_ms**: `float | None` 最新ネットワーク RTT（ミリ秒）
- **network_rtt_samples**: `int` (デフォルト: `0`) RTT サンプル数

---

## Instance クラス

インスタンスの状態を表すクラスです。設定とランタイム情報を保持します。

```python
from shogiarena.arena.instances.models import Instance
```

```python
Instance(
    config: InstanceConfig,
    metrics: InstanceMetrics = InstanceMetrics(),
    drain: bool = False,
    ...
)
```

### プロパティ

- **name**: `str` インスタンス名
- **type**: `InstanceType` インスタンス種別
- **config**: `InstanceConfig` 設定
- **metrics**: `InstanceMetrics` メトリクス
- **is_local**: `bool` ローカルインスタンスか
- **is_ssh**: `bool` SSH インスタンスか
- **effective_slots**: `int | None` 有効スロット数（設定値またはメトリクスから解決）
- **available_slots**: `int` 利用可能スロット数
- **available_engines**: `int` 利用可能エンジン数
- **max_engine_capacity**: `int` 最大同時エンジンプロセス数
- **can_accept_job**: `bool` ジョブを受け入れ可能か

### メソッド

#### acquire_resources()

リソース（スロットとエンジン容量）を確保します。

```python
def acquire_resources(*, slots: int = 0, engines: int = 0) -> bool
```

- **slots**: `int` (デフォルト: `0`) 必要な CPU スロット数
- **engines**: `int` (デフォルト: `0`) 必要なエンジンプロセス数

戻り値: リソースが確保できた場合は `True`

---

#### release_resources()

リソースを解放します。

```python
def release_resources(*, slots: int = 0, engines: int = 0) -> None
```

- **slots**: `int` (デフォルト: `0`) 解放するスロット数
- **engines**: `int` (デフォルト: `0`) 解放するエンジン数

---

#### update_metrics()

メトリクスを更新します。使用中のスロット数とエンジン数は保持されます。

```python
def update_metrics(new_metrics: InstanceMetrics) -> None
```

- **new_metrics**: `InstanceMetrics` 新しいメトリクス

---

## InstancePool クラス

インスタンスをスレッドセーフに管理するプールです。

```python
from shogiarena.arena.instances.pool import InstancePool
```

```python
InstancePool()
```

コンストラクタは引数なしで空のプールを作成します。通常はファクトリメソッドを使用してインスタンスを読み込みます。

### ファクトリメソッド

#### load_from_yaml()

YAML 設定ファイルからインスタンスプールを読み込みます。

```python
@classmethod
def load_from_yaml(cls, yaml_path: Path) -> InstancePool
```

- **yaml_path**: `Path` インスタンス設定 YAML ファイルのパス

---

#### load_default_local()

デフォルトのローカルインスタンス設定ファイルが存在する場合に読み込みます。

```python
@classmethod
def load_default_local(cls) -> InstancePool | None
```

ファイルが存在しない場合は `None` を返します。

---

### インスタンス管理

#### get_instance()

名前でインスタンスを取得します。

```python
def get_instance(name: str) -> Instance | None
```

- **name**: `str` インスタンス名

---

#### list_instances()

全インスタンスのリストを取得します。

```python
def list_instances() -> list[Instance]
```

---

#### get_local_instance()

ローカルインスタンスを取得します。

```python
def get_local_instance() -> Instance | None
```

---

#### add_instance()

インスタンスをプールに追加します。

```python
def add_instance(config: InstanceConfig, *, source_path: Path | None = None) -> Instance
```

- **config**: `InstanceConfig` インスタンス設定
- **source_path**: `Path | None` (デフォルト: `None`) 設定ファイルのパス

---

#### remove_instance()

インスタンスをプールから削除します。

```python
def remove_instance(name: str) -> Instance | None
```

- **name**: `str` 削除するインスタンス名

---

#### ensure_local_instance()

ローカルインスタンスが存在することを保証します。存在しない場合はデフォルトのローカルインスタンスをメモリ上に作成します。

```python
def ensure_local_instance() -> Instance
```

---

### リソース管理

#### try_acquire_resources()

複数インスタンスのリソースを原子的に確保します。いずれかのインスタンスで確保に失敗した場合はすべてロールバックされます。

```python
def try_acquire_resources(requirements: Mapping[str, ResourceRequest]) -> bool
```

- **requirements**: `Mapping[str, ResourceRequest]` インスタンス名からリソース要求へのマッピング

戻り値: すべてのリソースが確保できた場合は `True`

---

#### release_resources()

確保済みリソースを解放します。

```python
def release_resources(allocations: Mapping[str, ResourceRequest]) -> None
```

- **allocations**: `Mapping[str, ResourceRequest]` 解放対象のマッピング

---

## ResourceRequest クラス

リソース要求を表す不変データクラスです。

```python
from shogiarena.arena.instances.pool import ResourceRequest
```

```python
ResourceRequest(slots: int = 0, engines: int = 0)
```

### フィールド

- **slots**: `int` (デフォルト: `0`) 必要スロット数
- **engines**: `int` (デフォルト: `0`) 必要エンジン数

---

## スロットポリシー

### estimate_required_slots()

エンジン設定から必要スロット数を推定します。

```python
from shogiarena.arena.instances.slot_policy import estimate_required_slots
```

```python
def estimate_required_slots(engine_spec: Any, extra_options: Mapping[str, Any] | None = None) -> int
```

- **engine_spec**: `Any` エンジン設定オブジェクト（`options` 属性を持つ）
- **extra_options**: `Mapping[str, Any] | None` (デフォルト: `None`) 追加オプション（`engine_spec.options` より優先）

推定ロジック:

- `Threads` / `USI_Threads` オプションからスレッド数を取得
- Ponder が有効な場合（`USI_Ponder` / `Ponder` が true）はフルスレッド数
- Ponder が無効な場合は半分（切り上げ）

---

## 使用例

### インスタンスプールの使用

```python
from shogiarena.arena.instances.pool import InstancePool, ResourceRequest

# YAML から読み込み
pool = InstancePool.load_from_yaml("configs/instances.yaml")

# または、デフォルトのローカルインスタンスを使用
pool = InstancePool.load_default_local()

# インスタンス一覧
for instance in pool.list_instances():
    print(f"{instance.config.name}: {instance.available_slots} slots")

# リソースを確保
requirements = {"local": ResourceRequest(slots=4, engines=1)}
if pool.try_acquire_resources(requirements):
    try:
        # 対局を実行
        pass
    finally:
        pool.release_resources(requirements)
```

### SSH インスタンスの設定

```yaml
# configs/instances.yaml
# 単一インスタンスのショートハンド形式
name: remote-server
type: ssh
host: 192.168.1.100
user: shogi
port: 22
identity_file: ~/.ssh/id_rsa
slots: 32
max_engines: 8
project_root: ~/ShogiArena-remote
```

### スロット数の推定

```python
from shogiarena.arena.instances.slot_policy import estimate_required_slots

class EngineSpec:
    options = {"Threads": 8, "USI_Ponder": "true"}

slots = estimate_required_slots(EngineSpec())
print(f"必要スロット: {slots}")  # Ponder 有効なので 8
```

## 関連ドキュメント

- [リモート実行](../user-guide/remote-execution.md) - SSH インスタンスの設定
- [インスタンス設計](../technical/instances.md) - 内部アーキテクチャ
