# Runners API

トーナメント・SPSA チューニングを実行する Runner の API リファレンスです。

## 概要

Runner は実行の全体フローを管理します。

- **TournamentRunner**: トーナメント（Round-robin / Gauntlet）を実行
- **SprtRunner**: SPRT テストを実行（TournamentRunner を継承）
- **SpsaRunner**: SPSA チューニングを実行

---

## TournamentRunner クラス

トーナメントを実行するランナーです。

```python
from shogiarena.arena.runners.tournament_runner import TournamentRunner

TournamentRunner(
    config: TournamentRunConfig,
    *,
    instance_pool: InstancePool | None = None,
    storage: RunStorage,
    progress_reporter: ProgressReporter | None = None,
    dashboard_enabled: bool | None = None,
    no_resume: bool = False,
)
```

- **config**: `TournamentRunConfig` トーナメント設定
- **instance_pool**: `InstancePool | None` (デフォルト: `None`) エンジンインスタンスプール
- **storage**: `RunStorage` ストレージ（必須）
- **progress_reporter**: `ProgressReporter | None` (デフォルト: `None`) 進捗レポーター
- **dashboard_enabled**: `bool | None` (デフォルト: `None`) ダッシュボード有効化。`None` で設定に従う
- **no_resume**: `bool` (デフォルト: `False`) 途中再開を無効化

#### run()

```python
async def run() -> TournamentRunResult
```

非同期でトーナメントを実行し、結果を返します。

---

#### run_sync()

```python
def run_sync() -> TournamentRunResult
```

同期でトーナメントを実行し、結果を返します。

---

#### shutdown()

```python
def shutdown() -> None
```

実行を停止します。

---

### プロパティ

- **config**: `TournamentRunConfig` 設定
- **run_dir**: `Path` 実行ディレクトリ
- **scheduler**: `GameScheduler` スケジューラ

### 使用例

```python
from pathlib import Path

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import FilesystemRunStorage

config = TournamentRunConfig.from_yaml("configs/run/arena/tournament.yaml")
storage = FilesystemRunStorage(Path("runs/tournament"))
runner = TournamentRunner(config, storage=storage)
result = runner.run_sync()
```

---

## SprtRunner クラス

SPRT（Sequential Probability Ratio Test）を実行するランナーです。`TournamentRunner` を継承しています。

```python
from shogiarena.arena.runners import SprtRunner

SprtRunner(
    config: TournamentRunConfig,
    *,
    instance_pool: InstancePool | None = None,
    storage: RunStorage,
    progress_reporter: ProgressReporter | None = None,
    dashboard_enabled: bool | None = None,
    no_resume: bool = False,
)
```

`TournamentRunner` と同じ引数を受け取ります。`TournamentRunConfig` を使用します。

### 制約

- `config.sprt` が必須
- `config.engines` は **2つのみ**

### 使用例

```python
from shogiarena.arena.runners import SprtRunner

runner = SprtRunner(config, storage=storage)
result = runner.run_sync()

if result.sprt:
    print(f"Decision: {result.sprt.decision}")
    print(f"LLR: {result.sprt.llr}")
```

---

## SpsaRunner クラス

SPSA チューニングを実行するランナーです。

```python
from shogiarena.arena.runners import SpsaRunner

SpsaRunner(
    config: SpsaRunConfig,
    *,
    instance_pool: InstancePool | None = None,
    storage: RunStorage,
    progress_reporter: ProgressReporter | None = None,
    dashboard_enabled: bool | None = None,
    no_resume: bool = False,
)
```

- **config**: `SpsaRunConfig` SPSA チューニング設定
- **instance_pool**: `InstancePool | None` (デフォルト: `None`) エンジンインスタンスプール
- **storage**: `RunStorage` ストレージ（必須）
- **progress_reporter**: `ProgressReporter | None` (デフォルト: `None`) 進捗レポーター
- **dashboard_enabled**: `bool | None` (デフォルト: `None`) ダッシュボード有効化。`None` で設定に従う
- **no_resume**: `bool` (デフォルト: `False`) 途中再開を無効化

### 使用例

```python
from shogiarena.arena.configs.spsa import load_config_yaml
from shogiarena.arena.runners import SpsaRunner
from shogiarena.arena.storage import FilesystemRunStorage

config = load_config_yaml("configs/run/spsa/tune.yaml")
storage = FilesystemRunStorage(config.output_dir / "runs" / config.experiment_name)
runner = SpsaRunner(config, storage=storage)
result = runner.run_sync()
print(result.final_params)
```

---

## RunResultBase クラス

全ての結果クラスの基底クラスです。

```python
from shogiarena.arena.results import RunResultBase

@dataclass(slots=True, kw_only=True)
class RunResultBase:
    run_id: str
    run_dir: Path
    storage: RunStorage
    summary: Mapping[str, Any] | None = None
    config_snapshot: Mapping[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

- **run_id**: `str` 実行 ID
- **run_dir**: `Path` 実行ディレクトリ
- **storage**: `RunStorage` ストレージ
- **summary**: `Mapping[str, Any] | None` (デフォルト: `None`) サマリ情報
- **config_snapshot**: `Mapping[str, Any] | None` (デフォルト: `None`) 設定のスナップショット
- **started_at**: `datetime | None` (デフォルト: `None`) 開始日時
- **completed_at**: `datetime | None` (デフォルト: `None`) 完了日時

---

## TournamentRunResult クラス

トーナメント実行の結果です。`RunResultBase` を継承しています。

```python
from shogiarena.arena.results.tournament import TournamentRunResult
```

`RunResultBase` から継承されるフィールド（`run_id`, `run_dir`, `storage`, `summary`, `config_snapshot`, `started_at`, `completed_at`）に加え、以下の固有フィールドを持ちます。

- **tournament**: `TournamentResults` 集計結果
- **sprt**: `SprtResult | None` (デフォルト: `None`) SPRT 結果

---

## SpsaRunResult クラス

SPSA 実行の結果です。`RunResultBase` を継承しています。

```python
from shogiarena.arena.results.spsa import SpsaRunResult
```

`RunResultBase` から継承されるフィールド（`run_id`, `run_dir`, `storage`, `summary`, `config_snapshot`, `started_at`, `completed_at`）に加え、以下の固有フィールドを持ちます。

- **final_params**: `Mapping[str, float]` (デフォルト: `{}`) 最終パラメータ

> **注意**: `summary` フィールドは基底クラス `RunResultBase` から継承されます。`SpsaRunResult` 固有のフィールドではありません。

---

## ProgressReporter クラス

進捗通知のプロトコルです。

```python
from shogiarena.arena.runners.reporting import ProgressReporter
```

#### on_game_start()

```python
def on_game_start(payload: dict[str, Any]) -> None
```

対局開始時に呼び出されます。

---

#### on_game_complete()

```python
def on_game_complete(payload: dict[str, Any]) -> None
```

対局完了時に呼び出されます。

---

#### on_update()

```python
def on_update(payload: dict[str, Any]) -> None
```

更新時に呼び出されます。

---

#### finalize()

```python
def finalize(payload: dict[str, Any]) -> None
```

終了時に呼び出されます。

### 組み込み実装

- **NullProgressReporter**: 何もしない実装
- **TqdmProgressReporter**: tqdm を使って進捗バーを表示する実装

```python
from shogiarena.arena.runners.reporting import NullProgressReporter
from shogiarena.arena.runners.tqdm_reporter import TqdmProgressReporter
```

---

## SessionContextBuilder クラス

Runner 用の `SessionContext` を構築するヘルパーです。

```python
from shogiarena.arena.runners.session_context_builder import SessionContextBuilder
```

#### for_tournament()

```python
@staticmethod
def for_tournament(
    *,
    storage: RunStorage,
    num_workers: int,
    instance_pool: InstancePool | None,
    run_id: str,
    experiment_name: str,
    scheduler: str,
    games_per_pair: int,
    num_engines: int,
    dashboard_enabled: bool,
    services: Mapping[str, Any],
) -> SessionContext
```

トーナメント用の `SessionContext` を構築します。

- **storage**: `RunStorage` ストレージ
- **num_workers**: `int` ワーカー数
- **instance_pool**: `InstancePool | None` インスタンスプール
- **run_id**: `str` 実行 ID
- **experiment_name**: `str` 実験名
- **scheduler**: `str` スケジューラ種別
- **games_per_pair**: `int` ペアごとの対局数
- **num_engines**: `int` エンジン数
- **dashboard_enabled**: `bool` ダッシュボード有効化
- **services**: `Mapping[str, Any]` サービスマッピング

---

#### for_spsa()

```python
@staticmethod
def for_spsa(
    *,
    storage: RunStorage,
    num_workers: int,
    instance_pool: InstancePool | None,
    run_id: str,
    experiment_name: str,
    dashboard_enabled: bool,
    no_resume: bool,
    session_uuid: str,
    services: Mapping[str, Any],
) -> SessionContext
```

SPSA 用の `SessionContext` を構築します。

- **storage**: `RunStorage` ストレージ
- **num_workers**: `int` ワーカー数
- **instance_pool**: `InstancePool | None` インスタンスプール
- **run_id**: `str` 実行 ID
- **experiment_name**: `str` 実験名
- **dashboard_enabled**: `bool` ダッシュボード有効化
- **no_resume**: `bool` 途中再開を無効化
- **session_uuid**: `str` セッション UUID
- **services**: `Mapping[str, Any]` サービスマッピング

---

## 使用例

### 基本的なトーナメント実行

```python
from pathlib import Path

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import FilesystemRunStorage

# 設定を読み込み
config = TournamentRunConfig.from_yaml("tournament.yaml")

# ストレージを作成
storage = FilesystemRunStorage(Path("runs/tournament"))

# 実行
runner = TournamentRunner(config, storage=storage)
result = runner.run_sync()

# 結果を表示
for standing in result.tournament.standings:
    print(f"{standing.name}: {standing.wins}W-{standing.losses}L ({standing.elo:.0f})")
```

### 進捗表示付き実行

```python
from shogiarena.arena.runners.tqdm_reporter import TqdmProgressReporter

reporter = TqdmProgressReporter()
runner = TournamentRunner(config, storage=storage, progress_reporter=reporter)
result = runner.run_sync()
```

### ダッシュボードなしで実行

```python
runner = TournamentRunner(config, storage=storage, dashboard_enabled=False)
result = runner.run_sync()
```

### 途中再開を無効化

```python
runner = TournamentRunner(config, storage=storage, no_resume=True)
result = runner.run_sync()
```

## 関連ドキュメント

- [トーナメントガイド](../user-guide/tournaments.md) - トーナメント実行の詳細
- [SPSA ガイド](../user-guide/spsa.md) - SPSA チューニングの詳細
- [Runners & Orchestrators](../technical/runners-orchestrators.md) - 内部アーキテクチャ
