# Scheduler API

対局スケジューリングの API リファレンスです。

## 概要

Scheduler は対局の組み合わせ（`GameSpec`）を生成し、Runner/Orchestrator に供給します。

- **GameScheduler**: スケジューラの抽象基底クラス
- **SelfPlayScheduler**: 単一エンジンの自己対局
- **RoundRobinScheduler**: 総当たり戦
- **GauntletScheduler**: ガントレット戦
- **SwissScheduler**: スイス式（未実装）

---

## 型エイリアス

### SeedLike

```python
SeedLike = int | float | str | bytes | bytearray | None
```

スケジューラの `seed` 引数に渡せる型です。再現性のあるスケジュール生成に使用します。

---

## GameScheduler クラス

スケジューラの抽象基底クラスです。全てのスケジューラはこのクラスを継承します。

```python
from shogiarena.arena.scheduler.game_scheduler import GameScheduler
```

#### generate_schedule()

```python
def generate_schedule(
    self,
    engines: list[EngineConfig],
    games_per_pair: int,
    seed: SeedLike,
    initial_positions: InitialPositionConfig,
) -> list[GameSpec]
```

対局スケジュールを生成する抽象メソッドです。

- **engines**: `list[EngineConfig]` エンジン仕様のリスト
- **games_per_pair**: `int` ペアあたりの対局数
- **seed**: `SeedLike` 再現性のためのランダムシード
- **initial_positions**: `InitialPositionConfig` 初期局面の設定

---

#### get_total_games()

```python
def get_total_games(self, num_engines: int, games_per_pair: int) -> int
```

総対局数を計算する抽象メソッドです。

- **num_engines**: `int` エンジン数
- **games_per_pair**: `int` ペアあたりの対局数

---

## SelfPlayScheduler クラス

単一エンジンの自己対局スケジューラです。`GameScheduler` を継承しています。

```python
from shogiarena.arena.scheduler.game_scheduler import SelfPlayScheduler

scheduler = SelfPlayScheduler()
schedule = scheduler.generate_schedule(
    engines=[engine],  # 正確に1台のエンジン
    games_per_pair=100,
    seed=42,
    initial_positions=initial_positions,
)
```

### 特徴

- エンジンは正確に1台のみ指定可能（それ以外は `ValueError`）
- 同一エンジンが先手・後手の両方を担当
- `flip_policy="pair_both"` の場合、同一局面で先後を入れ替えた2局を生成
- 総対局数: `games_per_pair`

---

## RoundRobinScheduler クラス

総当たり戦のスケジューラです。`GameScheduler` を継承しています。

```python
from shogiarena.arena.scheduler.game_scheduler import RoundRobinScheduler

scheduler = RoundRobinScheduler()
schedule = scheduler.generate_schedule(
    engines=config.engines,
    games_per_pair=4,
    seed=42,
    initial_positions=config.rules.initial_positions,
)
```

### 特徴

- 全エンジンペアを `games_per_pair` 回対局（最低2台のエンジンが必要）
- `flip_policy` に従って先後を決定
- 総対局数: `n*(n-1)/2 * games_per_pair`

### flip_policy の動作

| 値 | 動作 |
|----|------|
| `alternate` | 偶数ゲームは A=先手、奇数ゲームは B=先手 |
| `random` | ランダムに決定 |
| `pair_both` | 同一局面で両方向対局 |

---

## GauntletScheduler クラス

ガントレット戦のスケジューラです。ベースラインエンジンと他のエンジンを対局させます。`GameScheduler` を継承しています。

```python
from shogiarena.arena.scheduler.game_scheduler import GauntletScheduler

GauntletScheduler(baseline_count: int = 1)
```

- **baseline_count**: `int` (デフォルト: `1`) ベースラインエンジンの数（1以上）

### 特徴

- 先頭 `baseline_count` 台がベースライン
- 残りのエンジンはベースラインとのみ対局
- `flip_policy="pair_both"` の場合、各対戦カードで先後入替の2局をミニシリーズとして交互に生成
- 総対局数: `baseline_count * (num_engines - baseline_count) * games_per_pair`

### 使用例

```python
scheduler = GauntletScheduler(baseline_count=1)
schedule = scheduler.generate_schedule(
    engines=config.engines,
    games_per_pair=10,
    seed=42,
    initial_positions=config.rules.initial_positions,
)
```

---

## SwissScheduler クラス

スイス式トーナメントのスケジューラです（現在未実装）。`GameScheduler` を継承しています。

`generate_schedule()` を呼び出すと `NotImplementedError` が発生します。`get_total_games()` は `log2(n)` ラウンドに基づく推定値を返します。

---

## create_scheduler 関数

文字列からスケジューラを生成するファクトリ関数です。

```python
from shogiarena.arena.scheduler.game_scheduler import create_scheduler

def create_scheduler(scheduler_type: str) -> GameScheduler
```

- **scheduler_type**: `str` スケジューラの種類

対応する文字列:

| 文字列 | スケジューラ | 備考 |
|--------|-------------|------|
| `"selfplay"` | `SelfPlayScheduler` | |
| `"round_robin"` | `RoundRobinScheduler` | |
| `"gauntlet"` | `GauntletScheduler` | `baseline_count=1` で生成 |
| `"swiss"` | -- | `NotImplementedError` を送出 |

不明な文字列を渡すと `ValueError` が発生します。

---

## GameSpec クラス

対局仕様を表すデータクラスです。

```python
from shogiarena.arena.configs.tournament import GameSpec
```

```python
@dataclass
class GameSpec:
    black_engine: str
    white_engine: str
    initial_sfen: str
    game_id: str
    round_num: int = 0
    assigned_instance_black: str | None = None
    assigned_instance_white: str | None = None
    require_install: bool = False
```

- **black_engine**: `str` 先手エンジン名
- **white_engine**: `str` 後手エンジン名
- **initial_sfen**: `str` 初期局面（SFEN 形式）
- **game_id**: `str` 対局 ID（自動生成）
- **round_num**: `int` (デフォルト: `0`) ラウンド番号
- **assigned_instance_black**: `str | None` (デフォルト: `None`) 先手側の優先インスタンス
- **assigned_instance_white**: `str | None` (デフォルト: `None`) 後手側の優先インスタンス
- **require_install**: `bool` (デフォルト: `False`) リモートインスタンスで環境準備が必要かどうか

#### create()

```python
@classmethod
def create(
    cls,
    black: str,
    white: str,
    sfen: str,
    round_num: int,
    seed: str,
    version: str = "v1",
) -> GameSpec
```

エンジン名、局面、ラウンド番号、シードから一意の `game_id` を自動生成して `GameSpec` を作成します。`game_id` は `g{round_num+1:04d}-{hash}` の形式になります。

---

## 使用例

### 基本的な使用

```python
from shogiarena.arena.scheduler.game_scheduler import create_scheduler
from shogiarena.arena.configs.tournament import TournamentRunConfig

config = TournamentRunConfig.from_yaml("tournament.yaml")

# スケジューラを作成
scheduler = create_scheduler(config.tournament.scheduler)

# スケジュールを生成
schedule = scheduler.generate_schedule(
    engines=config.engines,
    games_per_pair=config.tournament.games_per_pair,
    seed=config.tournament.seed,
    initial_positions=config.rules.initial_positions,
)

print(f"総対局数: {len(schedule)}")
for spec in schedule[:5]:
    print(f"{spec.game_id}: {spec.black_engine} vs {spec.white_engine}")
```

### 総対局数の計算

```python
scheduler = create_scheduler("round_robin")
total = scheduler.get_total_games(num_engines=4, games_per_pair=10)
print(f"4エンジン、10局/ペア: {total}局")  # 60局
```

### 自己対局

```python
from shogiarena.arena.scheduler.game_scheduler import create_scheduler

scheduler = create_scheduler("selfplay")
schedule = scheduler.generate_schedule(
    engines=[engine],  # 1台のみ
    games_per_pair=200,
    seed=42,
    initial_positions=initial_positions,
)
# 200局の自己対局
```

### ガントレット戦

```python
from shogiarena.arena.scheduler.game_scheduler import GauntletScheduler

# 2台のベースラインエンジン
scheduler = GauntletScheduler(baseline_count=2)
schedule = scheduler.generate_schedule(
    engines=engines,  # 5台のエンジン
    games_per_pair=20,
    seed=42,
    initial_positions=initial_positions,
)
# ベースライン2台 x チャレンジャー3台 x 20局 = 120局
```

### カスタム初期局面

```python
from shogiarena.arena.configs.base import InitialPositionConfig

# ファイルから初期局面を読み込み
initial_positions = InitialPositionConfig(
    type="file",
    source="opening_book.sfen",
    flip_policy="pair_both",
)

schedule = scheduler.generate_schedule(
    engines=config.engines,
    games_per_pair=100,
    seed=42,
    initial_positions=initial_positions,
)
```

## 関連ドキュメント

- [トーナメントガイド](../user-guide/tournaments.md) - トーナメント設定
- [開局集](../user-guide/opening-books.md) - 初期局面の設定
