# Services と Utilities

横断的な機能を提供するサービス層の設計を説明します。

## サービス層の役割

サービス層は、複数のレイヤーから利用される共通機能を提供します：

- **RatingService**: Elo レーティング計算
- **SprtService**: 統計的仮説検定
- **StatisticsService**: 対局統計の集計
- **DatabaseService**: データ永続化
- **AdjudicationService**: 投了・千日手判定

## RatingService

### 責務

- Elo レーティングの計算
- レーティング履歴の管理
- K 値の動的調整

### 実装

```python
class RatingService:
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

### K 値の動的調整

```python
class AdaptiveRatingService(RatingService):
    def get_k_factor(self, engine: str) -> float:
        """対局数に応じて K 値を調整"""
        games_played = self._game_counts.get(engine, 0)

        if games_played < 30:
            return 32  # 初期は大きく変動
        elif games_played < 100:
            return 24
        else:
            return 16  # 安定してきたら小さく
```

## SprtService

### 責務

- Log Likelihood Ratio (LLR) の計算
- 統計的仮説検定の判定
- 早期停止の判断

### 実装

```python
class SprtService:
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

    def calculate_llr(
        self,
        wins: int,
        losses: int,
        draws: int
    ) -> float:
        """Log Likelihood Ratio を計算"""
        # Elo差を勝率に変換
        p0 = self._elo_to_win_prob(self._elo0)
        p1 = self._elo_to_win_prob(self._elo1)

        # LLR の計算
        n = wins + losses + draws
        w = wins + 0.5 * draws  # 引き分けは0.5勝として計算

        llr = w * math.log(p1 / p0) + (n - w) * math.log((1 - p1) / (1 - p0))
        return llr

    def check_decision(self, llr: float) -> Literal["H0", "H1", "continue"]:
        """SPRT の判定"""
        if llr >= self._upper_bound:
            return "H1"  # 対立仮説を採択（差がある）
        elif llr <= self._lower_bound:
            return "H0"  # 帰無仮説を採択（差がない）
        else:
            return "continue"  # 継続

    @staticmethod
    def _elo_to_win_prob(elo_diff: float) -> float:
        """Elo差を勝率に変換"""
        return 1 / (1 + 10 ** (-elo_diff / 400))
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

## StatisticsService

### 責務

- 対局統計の集計
- 勝率、平均手数などの計算
- パフォーマンスメトリクスの収集

### 実装

```python
@dataclass
class EngineStatistics:
    wins: int = 0
    losses: int = 0
    draws: int = 0
    total_time_ms: int = 0
    total_nodes: int = 0
    average_move_time_ms: float = 0
    average_depth: float = 0

    @property
    def total_games(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def win_rate(self) -> float:
        if self.total_games == 0:
            return 0.0
        return self.wins / self.total_games

class StatisticsService:
    def aggregate_stats(
        self,
        engine: str,
        results: list[GameResult]
    ) -> EngineStatistics:
        """エンジンの統計を集計"""
        stats = EngineStatistics()

        for result in results:
            if result.winner == engine:
                stats.wins += 1
            elif result.winner is None:
                stats.draws += 1
            else:
                stats.losses += 1

            # 思考時間・ノード数の集計
            stats.total_time_ms += result.get_time_for_engine(engine)
            stats.total_nodes += result.get_nodes_for_engine(engine)

        # 平均値の計算
        if stats.total_games > 0:
            stats.average_move_time_ms = stats.total_time_ms / stats.total_games

        return stats
```

## DatabaseService

### 責務

- SQLite への結果保存
- クエリの最適化
- トランザクション管理

### スキーマ

```sql
CREATE TABLE games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    black_engine TEXT NOT NULL,
    white_engine TEXT NOT NULL,
    result TEXT CHECK(result IN ('black_win', 'white_win', 'draw')),
    total_moves INTEGER,
    start_time REAL,
    end_time REAL,
    initial_sfen TEXT,
    termination_reason TEXT
);

CREATE TABLE ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine_name TEXT NOT NULL,
    rating REAL NOT NULL,
    games_played INTEGER,
    timestamp REAL
);

CREATE TABLE moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(id),
    move_number INTEGER,
    move_usi TEXT,
    score_cp INTEGER,
    time_ms INTEGER,
    nodes INTEGER
);

CREATE INDEX idx_games_engines ON games(black_engine, white_engine);
CREATE INDEX idx_ratings_engine ON ratings(engine_name, timestamp);
```

### 実装

```python
class DatabaseService:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    async def save_game(self, result: GameResult) -> int:
        """対局結果を保存"""
        async with self._transaction():
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO games (black_engine, white_engine, result, total_moves,
                                   start_time, end_time, initial_sfen, termination_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.black_engine,
                    result.white_engine,
                    result.result,
                    len(result.moves),
                    result.start_time,
                    result.end_time,
                    result.initial_sfen,
                    result.termination_reason,
                ),
            )
            game_id = cursor.lastrowid

            # 指し手を保存
            for i, move in enumerate(result.moves):
                cursor.execute(
                    """
                    INSERT INTO moves (game_id, move_number, move_usi, score_cp, time_ms, nodes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (game_id, i + 1, move.usi, move.score_cp, move.time_ms, move.nodes),
                )

            return game_id

    async def get_rating_history(self, engine: str) -> list[tuple[float, float]]:
        """レーティング履歴を取得（timestamp, rating）"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT timestamp, rating FROM ratings WHERE engine_name = ? ORDER BY timestamp",
            (engine,),
        )
        return cursor.fetchall()
```

## AdjudicationService

### 責務

- 投了判定
- 千日手判定
- 引き分け判定

### 実装

```python
class AdjudicationService:
    def __init__(
        self,
        resign_score_cp: int = -1000,
        resign_move_count: int = 5,
        draw_score_cp: int = 10,
        draw_move_count: int = 40,
    ):
        self._resign_score_cp = resign_score_cp
        self._resign_move_count = resign_move_count
        self._draw_score_cp = draw_score_cp
        self._draw_move_count = draw_move_count

    def check_resignation(self, recent_scores: list[int]) -> bool:
        """投了判定"""
        if len(recent_scores) < self._resign_move_count:
            return False

        # 直近N手がすべて投了スコア以下
        return all(score <= self._resign_score_cp for score in recent_scores[-self._resign_move_count:])

    def check_draw(self, recent_scores: list[int]) -> bool:
        """引き分け判定"""
        if len(recent_scores) < self._draw_move_count:
            return False

        # 直近N手がすべて引き分けスコア範囲内
        return all(abs(score) <= self._draw_score_cp for score in recent_scores[-self._draw_move_count:])

    def check_repetition(self, position_history: list[str]) -> bool:
        """千日手判定"""
        if len(position_history) < 8:  # 最低4往復必要
            return False

        current_pos = position_history[-1]
        count = position_history.count(current_pos)

        return count >= 4  # 4回同じ局面で千日手
```

## ユーティリティ

### PathResolver

プレースホルダを含むパスを解決します。

```python
class PathResolver:
    def resolve(self, path_str: str, context: dict[str, Path]) -> Path:
        """プレースホルダを解決"""
        resolved = path_str

        for key, value in context.items():
            resolved = resolved.replace(f"{{{key}}}", str(value))

        # 環境変数も展開
        resolved = os.path.expandvars(resolved)

        return Path(resolved)
```

### TimeControl

持ち時間の管理と計算を行います。

```python
class TimeControl:
    def __init__(self, limits: TimeControlLimits):
        self._limits = limits
        self._remaining_ms: dict[str, int] = {}

    def allocate_time(self, engine: str, move_number: int) -> int:
        """次の手に割り当てる思考時間を計算"""
        if self._limits.fixed_time_ms:
            return self._limits.fixed_time_ms

        remaining = self._remaining_ms.get(engine, self._limits.time_ms)

        # 簡易的な時間配分（残り手数を推定）
        estimated_moves_left = max(60 - move_number, 10)
        allocated = remaining // estimated_moves_left

        # increment を考慮
        if self._limits.increment_ms:
            allocated += self._limits.increment_ms

        return allocated

    def update_remaining(self, engine: str, used_ms: int) -> None:
        """使用時間を減算"""
        remaining = self._remaining_ms.get(engine, self._limits.time_ms)
        self._remaining_ms[engine] = max(0, remaining - used_ms)

        # increment があれば加算
        if self._limits.increment_ms:
            self._remaining_ms[engine] += self._limits.increment_ms
```

## 次のステップ

- **[Architecture Overview](architecture.md)** - 全体像
- **[API Reference](../api/index.md)** - API ドキュメント
- **[Tournaments](../user-guide/tournaments.md)** - 設定方法
