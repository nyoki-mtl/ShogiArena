# Orchestrators API

並列実行を制御する Orchestrator の API リファレンスです。

## 概要

Orchestrator は Runner から発行された対局を並列実行し、エンジンプールや進捗管理を統括します。

- **BaseOrchestrator**: 全 Orchestrator の抽象基底クラス
- **TournamentOrchestrator**: トーナメントの並列実行
- **SpsaOrchestrator**: SPSA チューニングの並列実行
- **EnginePool**: エンジンのプール管理
- **ProgressHub**: 進捗の集約とブロードキャスト

---

## BaseOrchestrator クラス

全ての Orchestrator が継承する抽象基底クラスです。

```python
from shogiarena.arena.orchestrators import BaseOrchestrator

BaseOrchestrator(
    *,
    api_server: DashboardServerProtocol | None = None,
    summary_updater: SummaryUpdateCallback | None = None,
    session_context: SessionContext,
    hooks: GameLifecycleHooks,
    resource_poll_interval: float | None = None,
    resource_poll_max_interval: float | None = None,
)
```

全てキーワード専用引数です。

- **api_server**: `DashboardServerProtocol | None` (デフォルト: `None`) ダッシュボードサーバー。`broadcast_worker_update` や `set_worker_snapshot` メソッドを持つオブジェクトを渡す
- **summary_updater**: `SummaryUpdateCallback | None` (デフォルト: `None`) サマリー更新コールバック
- **session_context**: `SessionContext` セッションコンテキスト（実行ディレクトリ、ワーカー数、インスタンスプールなどを含む）
- **hooks**: `GameLifecycleHooks` ゲームライフサイクルフック
- **resource_poll_interval**: `float | None` (デフォルト: `None`) リソースポーリング間隔（秒）
- **resource_poll_max_interval**: `float | None` (デフォルト: `None`) リソースポーリング最大間隔（秒）

#### run()

```python
async def run(self) -> object
```

対局の実行を開始する抽象メソッドです。サブクラスで実装が必要です。

---

#### request_stop()

```python
def request_stop(self) -> None
```

Orchestrator に協調的な停止を要求します。新しい対局のスケジューリングを停止し、実行中の対局はそのまま継続します。

---

#### shutdown()

```python
async def shutdown(self) -> None
```

リソースをクリーンアップします。以下の処理を実行します:

- GameRunner に USI コマンド送信停止を通知
- プログレスコンシューマタスクをキャンセル
- ワーカータスク・実行中タスクをキャンセル
- EnginePool をシャットダウン

---

#### create_engine_pool()

```python
def create_engine_pool(self, max_instances_per_engine: int) -> EnginePool
```

指定された容量で EnginePool を作成してアタッチします。

- **max_instances_per_engine**: `int` エンジンあたりの最大インスタンス数

---

#### init_engine_pool_for_roles()

```python
def init_engine_pool_for_roles(self, num_workers: int, name_a: str, name_b: str) -> EnginePool
```

ロールペアのエンジン用に容量を最適化した EnginePool を初期化します。両方のロールが同じエンジン名を使用する場合、デッドロック防止のため容量が2倍になります。

- **num_workers**: `int` ワーカー数
- **name_a**: `str` ロール A のエンジン名
- **name_b**: `str` ロール B のエンジン名

---

#### get_engine_option_snapshots()

```python
def get_engine_option_snapshots(self) -> dict[str, dict[str, Any]]
```

USI オプションのスナップショットを取得します。

---

#### get_engine_info_snapshots()

```python
def get_engine_info_snapshots(self) -> dict[str, dict[str, str]]
```

エンジン情報のスナップショットを取得します。

---

### リモート実行支援メソッド

| メソッド | 説明 |
|---------|------|
| `get_remote_executor(instance)` | リモート実行器を取得 |
| `ensure_remote_repo(executor, instance)` | リモートリポジトリを準備 |
| `resolve_remote_binaries(executor, *paths)` | リモートバイナリを解決 |

---

## TournamentOrchestrator クラス

トーナメントの並列実行を担当します。`BaseOrchestrator` を継承しています。

```python
from shogiarena.arena.orchestrators import TournamentOrchestrator

TournamentOrchestrator(
    config: TournamentRunConfig,
    *,
    session: SessionContext,
    hooks: GameLifecycleHooks,
    summary_updater: SummaryUpdateCallback | None = None,
    api_server: Any | None = None,
    cancelled_provider: Callable[[], set[str]] | None = None,
)
```

`config` のみ位置引数で、それ以外はキーワード専用引数です。`session_context` ではなく `session` というパラメータ名を使用する点に注意してください。

- **config**: `TournamentRunConfig` トーナメント設定
- **session**: `SessionContext` Runner が準備したセッションコンテキスト（実行ディレクトリ、ワーカー数、プールを含む）
- **hooks**: `GameLifecycleHooks` Runner から提供されるライフサイクルフック
- **summary_updater**: `SummaryUpdateCallback | None` (デフォルト: `None`) ダッシュボードサマリーを更新するコルーチン
- **api_server**: `Any | None` (デフォルト: `None`) SSE ブロードキャスト用 API サーバー
- **cancelled_provider**: `Callable[[], set[str]] | None` (デフォルト: `None`) キャンセルされた対局 ID のセットを返すコールバック

#### set_schedule()

```python
def set_schedule(self, schedule: list[GameSpec], completed: set[str]) -> None
```

`run()` の前にスケジュールと完了済み対局セットを設定します。

- **schedule**: `list[GameSpec]` 対局スケジュール
- **completed**: `set[str]` 完了済み対局 ID のセット

---

#### run()

```python
async def run(self) -> TournamentResults
```

対局を並列実行します。`set_schedule()` で事前にスケジュールを設定しておく必要があります。

---

#### enqueue_restored_game()

```python
async def enqueue_restored_game(self, spec: GameSpec, display_order: int) -> None
```

再開対象の対局をキューに追加します。途中で中断した対局を復元する際に使用します。

- **spec**: `GameSpec` 再開する対局の仕様
- **display_order**: `int` 表示順序

---

## SpsaOrchestrator クラス

SPSA チューニングの並列実行を担当します。`BaseOrchestrator` を継承しています。

```python
from shogiarena.arena.orchestrators import SpsaOrchestrator

SpsaOrchestrator(
    config: SpsaRunConfig,
    *,
    session: SessionContext,
    hooks: GameLifecycleHooks,
    summary_updater: SummaryUpdateCallback | None = None,
    api_server: Any | None = None,
)
```

- **config**: `SpsaRunConfig` SPSA チューニング設定
- **session**: `SessionContext` Runner が準備したセッションコンテキスト
- **hooks**: `GameLifecycleHooks` Runner から提供されるライフサイクルフック
- **summary_updater**: `SummaryUpdateCallback | None` (デフォルト: `None`) サマリー更新コールバック
- **api_server**: `Any | None` (デフォルト: `None`) SSE ブロードキャスト用 API サーバー

#### set_update_items()

```python
def set_update_items(self, items: list[int], params: list[ParamEntry], sfens: list[str]) -> None
```

SPSA チューニングの更新アイテムを設定します。

- **items**: `list[int]` 更新アイテムインデックスのリスト（空不可）
- **params**: `list[ParamEntry]` パラメータエントリのリスト（空不可）。渡されたリストの要素の `not_used` フラグが変更される点に注意
- **sfens**: `list[str]` SFEN 形式の初期局面リスト（空不可）

---

#### run()

```python
async def run(self) -> None
```

SPSA 更新を並列実行します。`set_update_items()` で事前に設定しておく必要があります。

---

## EnginePool クラス

エンジンをキャッシュし、取得・返却・再利用を管理します。

```python
from shogiarena.arena.orchestrators.engine_pool import EnginePool

EnginePool(
    max_instances_per_engine: int = 2,
    *,
    engine_configs: Mapping[str, Any] | None = None,
    instance_pool: InstancePool | None = None,
    default_handshake_timeout: float | None = None,
)
```

- **max_instances_per_engine**: `int` (デフォルト: `2`) エンジンあたりの最大インスタンス数
- **engine_configs**: `Mapping[str, Any] | None` (デフォルト: `None`) エンジン名からエンジン仕様へのマッピング
- **instance_pool**: `InstancePool | None` (デフォルト: `None`) リモート/ローカルインスタンスの選択用プール
- **default_handshake_timeout**: `float | None` (デフォルト: `None`) ハンドシェイクのデフォルトタイムアウト（秒）

#### acquire()

```python
async def acquire(
    self,
    engine_name: str,
    config_path: Path,
    extra_options: dict[str, Any] | None = None,
    instance_override: str | None = None,
) -> AsyncUsiEngine
```

プールからエンジンを取得します。再利用可能なエンジンがあればそれを返し、なければ新規作成します。プールが容量上限に達している場合はスロットが空くまで待機します。

- **engine_name**: `str` エンジン名
- **config_path**: `Path` エンジン設定ファイルのパス
- **extra_options**: `dict[str, Any] | None` (デフォルト: `None`) 追加 USI オプション
- **instance_override**: `str | None` (デフォルト: `None`) 使用するインスタンスの明示的指定

---

#### release()

```python
async def release(
    self,
    engine_name: str,
    engine: AsyncUsiEngine,
    instance_override: str | None = None,
) -> None
```

エンジンをプールに返却します。プールに空きがありエンジンが実行中の場合は再利用のためプールに戻し、そうでなければ終了させます。

- **engine_name**: `str` エンジン名
- **engine**: `AsyncUsiEngine` 返却するエンジン
- **instance_override**: `str | None` (デフォルト: `None`) インスタンスの明示的指定

---

#### shutdown_all()

```python
async def shutdown_all() -> None
```

全てのエンジン（プール内と使用中の両方）をシャットダウンし、プールをクリアします。

---

#### acquire_pair_sorted()

```python
async def acquire_pair_sorted(
    self,
    a: tuple[str, Path, dict[str, Any] | None, str | None],
    b: tuple[str, Path, dict[str, Any] | None, str | None],
    second_timeout: float | None = None,
) -> tuple[AsyncUsiEngine, AsyncUsiEngine]
```

デッドロック回避のための順序付きペア取得です。スロットキーの辞書順で取得順序を決定します。2番目のエンジンの取得に失敗した場合、1番目のエンジンは自動的に返却されます。

- **a**: `tuple[str, Path, dict[str, Any] | None, str | None]` `(engine_name, config_path, extra_options, instance_override)` のタプル
- **b**: `tuple[str, Path, dict[str, Any] | None, str | None]` 同上
- **second_timeout**: `float | None` (デフォルト: `None`) 2番目のエンジン取得のタイムアウト（秒）

---

#### slot_key()

```python
@staticmethod
def slot_key(engine_key: str, instance_override: str | None) -> str
```

指定されたエンジン/インスタンスペアの内部プールスロットキーを返します。

---

## ProgressHub クラス

進捗を集約してダッシュボードにブロードキャストします。

```python
from shogiarena.arena.orchestrators.progress import ProgressHub

ProgressHub(
    *,
    api_server: DashboardServerProtocol | None,
    preassign_worker: Callable[[int, int, dict[int, int], set[int]], int | None],
)
```

全てキーワード専用引数です。

- **api_server**: `DashboardServerProtocol | None` ダッシュボードサーバー
- **preassign_worker**: `Callable[[int, int, dict[int, int], set[int]], int | None]` ゲーム ID にワーカーを事前割り当てするコールバック。引数は `(game_id_num, num_workers, game_to_worker, worker_busy)` で、割り当てたワーカー番号または `None` を返す

#### start()

```python
def start(
    self,
    *,
    num_workers: int,
    progress_queue: asyncio.Queue[tuple[int, int, str | None]],
    game_to_worker: dict[int, int],
    worker_busy: set[int],
    worker_snapshots: dict[int, dict[str, Any]],
    on_summary_update: SummaryUpdateCallback | None = None,
) -> None
```

プログレスコンシューマループをタスクとして開始します。

---

#### shutdown()

```python
async def shutdown(self) -> None
```

プログレスタスクをキャンセルし、サマリー更新タスクの完了を待ちます。

---

#### update_api_server()

```python
def update_api_server(self, api_server: DashboardServerProtocol | None) -> None
```

API サーバー参照を更新します。

---

#### consume_progress_loop()

```python
async def consume_progress_loop(
    self,
    progress_queue: asyncio.Queue[tuple[int, int, str | None]],
    state: ProgressState,
    on_summary_update: SummaryUpdateCallback | None = None,
) -> None
```

GameRunner からのプログレスイベントを消費し、SSE 更新を行う汎用コンシューマです。対応するイベントタイプ:

- `move_progress`: 指し手の進捗（評価値、ノード数、深度などを含む）
- `clock_start`: 持ち時間計測の開始
- `clock_increment`: 持ち時間の加算
- `game_assigned`: 対局のワーカーへの割り当て
- `handshake_log`: ハンドシェイクログ
- `engine_io`: エンジン I/O ログ

---

### ProgressState

```python
@dataclass
class ProgressState:
    num_workers: int
    game_to_worker: dict[int, int]
    worker_busy: set[int]
    worker_snapshots: dict[int, dict[str, Any]]
    worker_generation: dict[int, int] = field(default_factory=dict)
```

### SummaryUpdateCallback

```python
SummaryUpdateCallback = Callable[[], Coroutine[Any, Any, None]]
```

---

## ユーティリティ

### base_orchestrator_utils

Orchestrator の共通ユーティリティ関数群です。

```python
from shogiarena.arena.orchestrators.base_orchestrator_utils import (
    numeric_game_id,
    build_usi_options,
    build_time_control_limits,
    time_control_limits_to_dict,
    compute_time_control_from_rules,
    compute_max_ply_extra_options,
    create_game_runner_from_rules,
    build_engine_config_map,
    detect_git_remote_and_ref,
    create_progress_queue,
    max_plies_from_rules,
    remote_project_root,
    determine_event_move_count,
    enqueue_progress_event,
    make_initial_snapshot,
    apply_move_progress,
    build_move_diff_payload,
    build_clock_start_diff_payload,
    build_clock_increment_diff_payload,
    build_game_assigned_diff_payload,
    build_remote_game_info,
    compute_pool_capacity,
    make_role_pool_key,
)
```

#### numeric_game_id()

```python
def numeric_game_id(game_id: str | int) -> int
```

ゲーム識別子を安定した非負整数に変換します。`"game_001"` のような文字列は `1` に変換され、それ以外の文字列は CRC32 ハッシュで変換されます。

---

#### build_usi_options()

```python
def build_usi_options(base_extra: dict[str, Any] | None, engine_spec: Any) -> dict[str, Any] | None
```

Arena レベルの追加オプションとエンジン固有のオーバーレイ/オプションをマージします。

---

#### build_time_control_limits()

```python
def build_time_control_limits(base_tc: Any | None, override_tc: Any | None) -> TimeControlLimits | None
```

ベースの持ち時間設定とエンジン別オーバーライドを組み合わせて最終的な `TimeControlLimits` を構築します。バリデーション付きです。

---

#### compute_time_control_from_rules()

```python
def compute_time_control_from_rules(rules: Any, engines: list[Any]) -> tuple[TimeControlLimits | None, bool]
```

ルールとエンジンリストからグローバルな `TimeControlLimits` と有効フラグを計算します。

---

#### time_control_limits_to_dict()

```python
def time_control_limits_to_dict(limits: TimeControlLimits | None) -> dict[str, int | bool | None]
```

`TimeControlLimits` を JSON シリアライズ可能な辞書に変換します。

---

#### create_game_runner_from_rules()

```python
def create_game_runner_from_rules(
    rules: Any,
    engines: list[Any],
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
) -> GameRunner
```

トーナメント/SPSA ルールから `GameRunner` を構築します。裁定設定（投了判定、最大手数）も含みます。

---

#### build_engine_config_map()

```python
def build_engine_config_map(engines: list[Any]) -> dict[str, Any]
```

エンジンリストからエンジン名をキーとするマップを構築します。

---

#### compute_pool_capacity()

```python
def compute_pool_capacity(num_workers: int, same_name: bool) -> int
```

ワーカー数とエンジンの対称性に基づいてプール容量を計算します。同名エンジンの場合は容量を2倍にします。最小値は `2` です。

---

#### make_initial_snapshot()

```python
def make_initial_snapshot(
    progress: dict[str, Any],
    generation: int | None = None,
    name_default: str = "Unknown",
) -> dict[str, Any]
```

プログレスペイロードからワーカースナップショットの初期構造を作成します。

---

#### create_progress_queue()

```python
def create_progress_queue() -> asyncio.Queue[tuple[int, int, str | None]]
```

Orchestrator 間で共有されるプログレスキューのファクトリ関数です。

---

#### enqueue_progress_event()

```python
def enqueue_progress_event(
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
    game_id: str,
    event: dict[str, Any],
    *,
    fallback_move_count: int = 0,
    allow_default_str: bool = False,
) -> None
```

リモートイベントをシリアライズしてダッシュボードプログレスキューに追加します。

---

#### make_role_pool_key()

```python
def make_role_pool_key(name: str, role: str) -> str
```

ロールが割り当てられたエンジンを区別するためのプールキーを返します。

---

## 使用例

### TournamentOrchestrator の使用

通常は `TournamentRunner` 経由で使用しますが、直接使用する場合:

```python
from shogiarena.arena.orchestrators import TournamentOrchestrator
from shogiarena.arena.scheduler import create_scheduler

# スケジューラでスケジュールを生成
scheduler = create_scheduler("round_robin")
schedule = scheduler.generate_schedule(
    engines=config.engines,
    games_per_pair=config.tournament.games_per_pair,
    seed=config.tournament.seed,
    initial_positions=config.rules.initial_positions,
)

# Orchestrator を作成
orchestrator = TournamentOrchestrator(
    config=config,
    session=ctx,
    hooks=hooks,
)

# スケジュールを設定して実行
orchestrator.set_schedule(schedule, completed=set())
await orchestrator.run()
```

### EnginePool の使用

```python
from shogiarena.arena.orchestrators.engine_pool import EnginePool
from pathlib import Path

pool = EnginePool(
    max_instances_per_engine=4,
    engine_configs=engine_config_map,
    instance_pool=instance_pool,
)

# エンジンを取得
engine = await pool.acquire("engine1", Path("configs/engine/engine1.yaml"))

# 対局を実行
# ...

# エンジンを返却（再利用可能な場合はプールに戻る）
await pool.release("engine1", engine)

# クリーンアップ
await pool.shutdown_all()
```

## 関連ドキュメント

- [Runners API](runners.md) - Runner との連携
- [Runners & Orchestrators](../technical/runners-orchestrators.md) - 内部アーキテクチャ
