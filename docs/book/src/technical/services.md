# Services と Utilities

横断的な機能を提供するサービス層の設計を説明します。

## サービス層の構造

サービス層は、複数のレイヤーから利用される共通機能を提供します：

```text
arena/services/
├── statistics/              # 統計・レーティング
│   ├── rating_service.py    # EloRatingService
│   ├── sprt.py              # Sprt (SPRT 統計検定)
│   ├── pentanomial.py       # ペンタノミアルモデル
│   └── btd.py               # BTDEstimator
├── persistence/             # データ永続化
│   ├── db_service.py        # ArenaDBService
│   ├── result_store.py      # ResultStore / RunStorageResultStore
│   └── records.py           # 棋譜関連レコード
├── game_control/            # 対局制御
│   └── adjudication.py      # Adjudicator (千日手・投了判定)
├── artifacts/               # アーティファクト管理
│   ├── resolver.py          # ArtifactResolver
│   └── builder.py           # ビルダー
└── openbench.py             # OpenBenchClient (外部連携)
```

## EloRatingService

### 責務

- Elo レーティングの計算
- レーティング履歴の管理
- K 値の動的調整

### 実装

```python
class EloRatingService:
    def __init__(self, initial_rating: float = 1500, k_factor: float = 16):
        self._initial_rating = initial_rating
        self._k_factor = k_factor
        self._ratings: dict[str, float] = {}

    def update_ratings(
        self,
        engine1: str,
        engine2: str,
        result: Literal["win", "loss", "draw"]
    ) -> dict[str, float]:
        """対局結果からレーティングを更新"""
        rating1 = self._ratings.get(engine1, self._initial_rating)
        rating2 = self._ratings.get(engine2, self._initial_rating)

        # 期待勝率の計算
        expected1 = 1 / (1 + 10 ** ((rating2 - rating1) / 400))
        expected2 = 1 - expected1

        # 実際の得点
        score1 = {"win": 1.0, "loss": 0.0, "draw": 0.5}[result]
        score2 = 1.0 - score1

        # レーティング更新
        new_rating1 = rating1 + self._k_factor * (score1 - expected1)
        new_rating2 = rating2 + self._k_factor * (score2 - expected2)

        self._ratings[engine1] = new_rating1
        self._ratings[engine2] = new_rating2

        return {engine1: new_rating1, engine2: new_rating2}
```

### Elo レーティングの数学

**期待勝率の計算**:

$$
E_A = \frac{1}{1 + 10^{(R_B - R_A) / 400}}
$$

**レーティング更新**:

$$
R'_A = R_A + K \cdot (S_A - E_A)
$$

ここで：

- $R_A, R_B$: 現在のレーティング
- $E_A$: 期待勝率
- $S_A$: 実際の得点（勝ち=1, 引き分け=0.5, 負け=0）
- $K$: K 値（更新の感度）

## Sprt (SPRT 統計検定)

### 責務

- Log Likelihood Ratio (LLR) の計算
- 統計的仮説検定の判定
- 早期停止の判断

### 実装

```python
class SprtDecision(Enum):
    H0 = "H0"          # 帰無仮説を採択（差がない）
    H1 = "H1"          # 対立仮説を採択（差がある）
    CONTINUE = "continue"  # 継続

@dataclass
class SprtResult:
    llr: float
    decision: SprtDecision
    lower_bound: float
    upper_bound: float

class Sprt:
    def __init__(
        self,
        elo0: float,
        elo1: float,
        alpha: float = 0.05,
        beta: float = 0.05
    ):
        self._elo0 = elo0
        self._elo1 = elo1
        self._alpha = alpha
        self._beta = beta

        # 閾値の計算
        self._lower_bound = math.log(beta / (1 - alpha))
        self._upper_bound = math.log((1 - beta) / alpha)
```

### SPRT の数学

**LLR の計算**:

$$
\text{LLR} = \sum_{i=1}^{n} \log \frac{P(x_i | H_1)}{P(x_i | H_0)}
$$

**判定**:

- $\text{LLR} \geq \log\frac{1-\beta}{\alpha}$: H1 採択
- $\text{LLR} \leq \log\frac{\beta}{1-\alpha}$: H0 採択
- それ以外: 継続

## ArenaDBService (永続化)

### 責務

- SQLAlchemy を使用した SQLite への結果保存
- 対局結果、棋譜、エンジン情報の永続化
- クエリの最適化

### データモデル

データベースモデルは `db/models.py` で SQLAlchemy の `DeclarativeBase` を使って定義されています：

```python
class Base(DeclarativeBase):
    pass

class Player(Base):
    __tablename__ = "players"
    # エンジン（プレイヤー）情報

class Game(Base):
    __tablename__ = "games"
    # 対局結果（先手/後手エンジン、勝敗、手数、時間制御など）

class Kifu(Base):
    __tablename__ = "kifu"
    # 棋譜データ（フォーマット別）

class EngineArtifact(Base):
    __tablename__ = "engine_artifacts"
    # エンジンバイナリのビルド情報

class InstanceSpec(Base):
    __tablename__ = "instance_specs"
    # 実行インスタンスの仕様

class GameInstanceParticipation(Base):
    __tablename__ = "game_instance_participations"
    # 対局とインスタンスの関連
```

### リポジトリパターン

`db/repository.py` の `ShogiRepository` がクエリロジックを集約し、`db/factory.py` の `SQLiteShogiDBFactory` がデータベース接続を管理します。

## Adjudicator (対局制御)

### 責務

- 投了判定（評価値ベース）
- 千日手判定
- 引き分け判定（最大手数）

### 実装

```python
class AdjudicationResult(Enum):
    CONTINUE = "continue"
    RESIGN = "resign"
    DRAW = "draw"
    MAX_PLY = "max_ply"

@dataclass
class AdjudicationConfig:
    resign_score_cp: int = -1000
    resign_move_count: int = 5
    draw_score_cp: int = 10
    draw_move_count: int = 40
    max_ply: int | None = None

class Adjudicator:
    """投了・千日手・引き分けの判定を行う"""
    ...
```

内部では `ResignTracker` と `MaxPlyTracker` が連手のスコア追跡と最大手数の監視を行います。

## ArtifactResolver (アーティファクト管理)

### 責務

- エンジンバイナリのビルド管理
- リポジトリからのソース取得
- ビルドキャッシュ

```python
class ArtifactResolver:
    """エンジンバイナリの解決（ビルドまたはキャッシュ参照）"""
    ...
```

## ユーティリティ

### パス管理 (`utils/common/`)

```text
utils/common/
├── settings.py          # アプリケーション設定 (output_dir, engine_dir 等)
├── project_dirs.py      # プロジェクトディレクトリの解決
├── run_paths.py         # 実行パスの構築
├── paths.py             # パスユーティリティ
├── logging.py           # ロギング設定
└── locks.py             # ファイルロック管理
```

- **settings.py**: `shogiarena init` で設定したディレクトリ情報の読み込み、プレースホルダー (`{output_dir}`, `{engine_dir}`) の解決
- **project_dirs.py**: プラットフォーム別のデフォルトパス（XDG Base Directory 準拠）
- **run_paths.py**: トーナメント実行ディレクトリの構成

### 型定義 (`utils/types/`)

共通の型定義を集約しています。

### CPU 情報 (`cpuinfo.py`)

CPU のコア数やアーキテクチャ情報を取得し、並列実行の最適化に使用します。

## 次のステップ

- **[Architecture Overview](architecture.md)** - 全体像
- **[API Reference](../api/index.md)** - API ドキュメント
- **[Tournaments](../user-guide/tournaments.md)** - 設定方法
