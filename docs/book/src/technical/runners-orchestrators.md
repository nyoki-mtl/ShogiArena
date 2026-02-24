# Runner と Orchestrator

対局実行を管理する Runner と Orchestrator の設計思想と役割分担を説明します。

## 役割分担の哲学

### Runner: トーナメント全体のライフサイクル

**責務**:

- 受け取った設定の整合性チェック
- エンジンの初期化
- 結果の集計とレポート生成
- トーナメント全体の進行管理

**Runner が関与しないこと**:

- 個別の対局実行
- エンジンとの直接通信
- 並列実行の詳細

### Orchestrator: 対局の並列実行管理

**責務**:

- 対局スケジュールの生成
- ワーカープールの管理
- 対局の並列実行
- リソース割り当て

**Orchestrator が関与しないこと**:

- レーティング計算
- 結果の永続化（DB への保存）
- UI への通知

### この分離の利点

1. **テストしやすい**: 各レイヤーを独立してテスト可能
2. **拡張しやすい**: 新しいトーナメント方式を簡単に追加
3. **並列化しやすい**: Orchestrator が並列実行の複雑さを隠蔽
4. **再利用しやすい**: Orchestrator は他のシステムでも使える

## Runner の種類

### TournamentRunner

一般的なラウンドロビンやガントレット方式のトーナメントを実行します。

**主要メソッド**:

```python
class TournamentRunner:
    async def run(self) -> None:
        """トーナメント全体を実行"""
        # 1. スケジュール生成
        schedule = self._generate_schedule()

        # 2. Orchestrator に実行依頼
        await self._run_orchestrator(schedule)

        # 3. 結果集計とレポート
        await self._finalize()
```

**設定例**:

```yaml
tournament:
  scheduler: round_robin
  games_per_pair: 100
  num_parallel: 4
```

### SprtRunner

SPRT（逐次確率比検定）による統計的検定を実行します。

**特徴**:

- 統計的に有意な結果が得られた時点で自動終了
- LLR（Log Likelihood Ratio）をリアルタイムで計算
- H0（帰無仮説）または H1（対立仮説）の採択判定

**主要メソッド**:

```python
class SprtRunner(TournamentRunner):
    async def _check_early_stop(self) -> bool:
        """SPRT 判定で早期終了可能か確認"""
        llr = self._sprt_service.calculate_llr(self._results)

        if llr >= self._upper_bound:
            logger.info("SPRT: H1 accepted (difference detected)")
            return True
        if llr <= self._lower_bound:
            logger.info("SPRT: H0 accepted (no significant difference)")
            return True

        return False  # 継続
```

**設定例**:

```yaml
sprt:
  elo0: 0       # 帰無仮説
  elo1: 10      # 対立仮説
  alpha: 0.05
  beta: 0.05
  min_games: 0
```

### SpsaRunner

SPSA によるパラメータチューニングを実行します。

**特徴**:

- 基準エンジンとチューニング対象を並列に対局
- スコア差からパラメータを更新
- ダッシュボードで推移を可視化

## Orchestrator の設計

### MatchOrchestrator

対局の並列実行を管理します。

**主要な設計決定**:

1. **ワーカープール**: `asyncio.Semaphore` で並列数を制御
2. **タスクキュー**: 対局をキューに積み、ワーカーが取得
3. **フェアネス**: エンジン間でリソースを公平に配分

**コア実装**:

```python
class MatchOrchestrator:
    async def run_matches(
        self,
        schedule: list[MatchSpec],
        engines: dict[str, AsyncUsiEngine]
    ) -> list[GameResult]:
        """対局を並列実行"""
        semaphore = asyncio.Semaphore(self._num_parallel)

        async def run_single_match(spec: MatchSpec) -> GameResult:
            async with semaphore:  # 並列数を制御
                return await self._game_runner.run_game(
                    black_engine=engines[spec.black],
                    white_engine=engines[spec.white],
                    initial_sfen=spec.sfen
                )

        # すべての対局を並列実行
        tasks = [run_single_match(spec) for spec in schedule]
        return await asyncio.gather(*tasks)
```

### スケジューラーとの連携

**Scheduler の役割**:

- 対局の組み合わせを生成
- 開始局面の割り当て
- 先後の決定

**主要なスケジューラー**:

#### RoundRobinScheduler

すべてのエンジンが互いに対局します。

```python
class RoundRobinScheduler:
    def generate(self, engines: list[str], games_per_pair: int) -> list[MatchSpec]:
        matches = []
        for i, eng1 in enumerate(engines):
            for eng2 in engines[i+1:]:
                for _ in range(games_per_pair // 2):
                    matches.append(MatchSpec(black=eng1, white=eng2))
                    matches.append(MatchSpec(black=eng2, white=eng1))
        return matches
```

#### GauntletScheduler

基準エンジンに対して他のエンジンが挑戦します。

```python
class GauntletScheduler:
    def generate(
        self,
        baseline: str,
        challengers: list[str],
        games_per_pair: int
    ) -> list[MatchSpec]:
        matches = []
        for challenger in challengers:
            for _ in range(games_per_pair // 2):
                matches.append(MatchSpec(black=baseline, white=challenger))
                matches.append(MatchSpec(black=challenger, white=baseline))
        return matches
```

### 対局順序の最適化

**game_order の種類**:

#### pairwise

同じペアの対局をまとめて実行します。

**利点**: キャッシュヒット率が高い

**欠点**: レーティング収束が遅い

#### interleave

ペアを交互に実行します。

**利点**: レーティングが早く収束する

**欠点**: エンジンの切り替えが多い

#### shuffle

ランダムに実行します。

**利点**: 偏りがない

**欠点**: 予測しにくい

```python
def optimize_order(schedule: list[MatchSpec], strategy: str) -> list[MatchSpec]:
    if strategy == "pairwise":
        return sorted(schedule, key=lambda m: (m.black, m.white))
    elif strategy == "interleave":
        return interleave_pairs(schedule)
    elif strategy == "shuffle":
        return random.shuffle(schedule)
    else:  # auto
        return choose_best_strategy(schedule)
```

## リソース管理

### インスタンスプール

エンジンを実行する環境（ローカル/SSH）を管理します。

```python
class InstancePool:
    def assign_instance(self, engine_name: str) -> Instance:
        """エンジンにインスタンスを割り当て"""
        # 明示的な割り当てがあればそれを使用
        if engine_name in self._assignments:
            return self._instances[self._assignments[engine_name]]

        # ラウンドロビンで自動割り当て
        return self._round_robin_assign()
```

### CPU アフィニティ

```python
class LocalInstance:
    def set_affinity(self, pid: int, cores: tuple[int, ...]) -> None:
        """プロセスを特定のCPUコアに固定"""
        import psutil
        proc = psutil.Process(pid)
        proc.cpu_affinity(cores)
```

## エラーハンドリングと回復

### 対局失敗時の処理

```python
async def _run_match_with_retry(self, spec: MatchSpec, max_retries: int = 3) -> GameResult:
    """対局を実行、失敗時はリトライ"""
    for attempt in range(max_retries):
        try:
            return await self._game_runner.run_game(spec)
        except Exception as e:
            logger.warning(f"Match failed (attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(1.0 * (2 ** attempt))  # Exponential backoff
```

### エンジンクラッシュ時の処理

```python
async def _handle_engine_crash(self, engine_name: str) -> None:
    """エンジンがクラッシュした場合の処理"""
    logger.error(f"Engine {engine_name} crashed, restarting...")

    # 古いエンジンを閉じる
    await self._engines[engine_name].close()

    # 新しいインスタンスを起動
    self._engines[engine_name] = await EngineFactory.create_engine(
        self._engine_paths[engine_name]
    )
    await self._engines[engine_name].start()
```

## パフォーマンス監視

### 対局時間の追跡

```python
@dataclass
class MatchTiming:
    start_time: float
    end_time: float
    total_moves: int
    black_time_ms: int
    white_time_ms: int

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time
```

### スループット計算

```python
class TournamentMetrics:
    def calculate_throughput(self) -> float:
        """1時間あたりの対局数を計算"""
        elapsed_hours = (time.time() - self._start_time) / 3600
        return self._completed_games / elapsed_hours
```

## 次のステップ

- **[Services と Utilities](services.md)** - サービス層の詳細
- **[USI Engine の設計](usi-engine.md)** - エンジン層の詳細
- **[Architecture Overview](architecture.md)** - 全体像
