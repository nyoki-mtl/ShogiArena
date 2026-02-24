# Execution API

対局実行レイヤーの API リファレンスです。

## 概要

Execution API は 1 局の対局を実行するための低レベル API を提供します。

- **GameRunner**: 対局の実行を担当
- **EngineParticipant**: エンジンを対局参加者として抽象化
- **GameEngineProtocol**: エンジンが満たすべきプロトコル

## GameRunner クラス

```python
shogiarena.arena.execution.game_runner.GameRunner(
    progress_queue=None,
    time_control_limits=None,
    adjudication_config=None,
    *,
    repetition_occurrences_to_draw=2,
    on_engine_options=None,
)
```

1 局の対局を実行するクラスです。2 つのエンジン（`GameEngineProtocol` 実装）を受け取り、
USI プロトコルに従って対局を進行し、`rshogi.record.GameRecord` を返します。

GameRunner は以下の機能を持ちます：

- 先手・後手エンジンの自動対局実行
- 持ち時間管理（先後独立設定可能）
- 投了・最大手数などの判定（adjudication）
- 千日手判定
- 進捗キューへのイベント送信

### 引数

- **progress_queue**: `asyncio.Queue[tuple[int, int, str | None]] | None` (デフォルト: `None`)
  進捗更新を送信するキュー。`(game_id, ply_index, description)` のタプルを送信します。
- **time_control_limits**: `TimeControlLimits | None` (デフォルト: `None`)
  デフォルトの持ち時間設定。`run_game` で先手・後手個別に上書き可能です。
- **adjudication_config**: `AdjudicationConfig | None` (デフォルト: `None`)
  投了・最大手数の判定設定。
- **repetition_occurrences_to_draw**: `int` (デフォルト: `2`)
  千日手と判定する同一局面の出現回数（2 以上）。
- **on_engine_options**: `Callable[[str, dict[str, object], dict[str, str] | None], None] | None` (デフォルト: `None`)
  USI オプションスナップショット取得時に呼ばれるコールバック。

### メソッド

#### `run_game()`

```python
await runner.run_game(
    black_engine: GameEngineProtocol,
    white_engine: GameEngineProtocol,
    initial_sfen: str = "startpos",
    game_id: str | None = None,
    black_time_control_limits: TimeControlLimits | None = None,
    white_time_control_limits: TimeControlLimits | None = None,
) -> rshogi.record.GameRecord
```

2 エンジンの対局を実行して結果を返します。

**引数:**
- **black_engine**: `GameEngineProtocol` 先手エンジン
- **white_engine**: `GameEngineProtocol` 後手エンジン
- **initial_sfen**: `str` (デフォルト: `"startpos"`) 初期局面（SFEN 形式）
- **game_id**: `str | None` (デフォルト: `None`) 対局 ID
- **black_time_control_limits**: `TimeControlLimits | None` (デフォルト: `None`) 先手の持ち時間（省略時はコンストラクタのデフォルト使用）
- **white_time_control_limits**: `TimeControlLimits | None` (デフォルト: `None`) 後手の持ち時間（省略時はコンストラクタのデフォルト使用）

**戻り値:**
- `rshogi.record.GameRecord`: 対局結果を含む棋譜データ

**例外:**
- `RuntimeError`: 時間制御が未設定
- `ValueError`: 不正な SFEN

---

#### `request_shutdown()`

```python
runner.request_shutdown() -> None
```

対局のシャットダウンを要求します。

---

## EngineParticipant クラス

```python
shogiarena.arena.execution.engine_participant.EngineParticipant(
    engine: AsyncUsiEngine,
    *,
    name_override: str | None = None,
    role: Literal["black", "white"] | None = None,
)
```

`AsyncUsiEngine` を `GameEngineProtocol` に適合させるラッパーです。

### 引数

- **engine**: `AsyncUsiEngine`
  ラップするエンジンインスタンス。
- **name_override**: `str | None` (デフォルト: `None`)
  エンジン名の上書き。`None` の場合はエンジン自身の名前を使用します。
- **role**: `Literal["black", "white"] | None` (デフォルト: `None`)
  役割の指定。

### メソッド

#### `prepare()`

```python
await participant.prepare(*, initial_sfen: str) -> None
```

対局準備を行います。`usinewgame` の送信と `isready` の確認を行います。

---

#### `think()`

```python
await participant.think(
    *,
    sfen: str,
    moves: Sequence[str],
    request: UsiThinkRequest,
    info_handler: InfoHandler | None = None,
    timeout: float | None = None,
) -> UsiThinkResult
```

思考を実行します。

---

#### `think_mate()`

```python
await participant.think_mate(
    *,
    sfen: str,
    moves: Sequence[str],
    ply_limit: int | None = None,
    node_limit: int | None = None,
    infinite: bool = False,
    info_handler: InfoHandler | None = None,
    wait_for_bestmove: bool | None = None,
    timeout: float | None = None,
) -> UsiMateResult
```

詰み探索を実行します。

---

#### `analyze()`

```python
await participant.analyze(
    *,
    sfen: str,
    moves: Sequence[str],
    request: UsiThinkRequest,
    info_handler: InfoHandler | None = None,
) -> AnalysisHandle
```

解析を開始します。

---

#### `start_ponder()`

```python
await participant.start_ponder(
    *,
    sfen: str,
    moves: Sequence[str],
    request: UsiThinkRequest,
    predicted_move: str | None,
    info_handler: InfoHandler | None = None,
    enable_early_ponder: bool | None = None,
) -> None
```

Ponder を開始します。

---

#### `ponder_hit()`

```python
await participant.ponder_hit(
    *,
    timings: PonderHitTimings | None,
    timeout: float | None = None,
) -> UsiThinkResult | None
```

Ponder ヒットを送信します。

---

#### `cancel_ponder()`

```python
await participant.cancel_ponder(*, timeout: float | None = None) -> UsiThinkResult | None
```

Ponder をキャンセルします。

---

#### `notify_gameover()`

```python
await participant.notify_gameover(result: GameResult) -> None
```

対局終了を通知します。

---

#### `shutdown()`

```python
await participant.shutdown() -> None
```

エンジンをシャットダウンします。

---

#### `has_active_ponder()`

```python
participant.has_active_ponder() -> bool
```

アクティブな Ponder があるか判定します。

---

#### `active_ponder_predicted_move()`

```python
participant.active_ponder_predicted_move() -> str | None
```

Ponder で予測している手を返します。

---

## GameEngineProtocol

```python
shogiarena.arena.execution.types.GameEngineProtocol
```

`GameRunner` が要求する最小インターフェースです。`@runtime_checkable` なプロトコルとして定義されています。

このプロトコルを実装することで、カスタムエンジンを `GameRunner` で使用できます。

### 必須プロパティ

- **`name`**: `str` エンジン名

### 必須メソッド

- `prepare(*, initial_sfen)` - 対局準備
- `think(*, sfen, moves, request, info_handler, timeout)` - 思考
- `think_mate(*, sfen, moves, ply_limit, ...)` - 詰み探索
- `analyze(*, sfen, moves, request, info_handler)` - 解析
- `notify_gameover(result)` - 対局終了通知
- `stop()` - 思考停止
- `shutdown()` - シャットダウン
- `get_usi_options_snapshot()` - USI オプション取得
- `get_engine_info_snapshot()` - エンジン情報取得
- `start_ponder(*, sfen, moves, request, predicted_move, ...)` - Ponder 開始
- `ponder_hit(*, timings, timeout)` - Ponder ヒット
- `cancel_ponder(*, timeout)` - Ponder キャンセル
- `has_active_ponder()` - Ponder 状態確認
- `active_ponder_predicted_move()` - Ponder 予測手取得

## 使用例

### 基本的な対局実行

```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.execution.game_runner import GameRunner
from shogiarena.arena.execution.engine_participant import EngineParticipant

async def run_single_game():
    # エンジンを作成
    engine1 = await EngineFactory.create_engine("engine1.yaml")
    engine2 = await EngineFactory.create_engine("engine2.yaml")

    await engine1.start()
    await engine2.start()

    # 参加者としてラップ
    black = EngineParticipant(engine1)
    white = EngineParticipant(engine2)

    # GameRunner を作成
    runner = GameRunner(
        time_control_limits=TimeControlLimits(byoyomi_ms=1000),
    )

    # 対局を実行
    game_record = await runner.run_game(
        black_engine=black,
        white_engine=white,
        initial_sfen="startpos",
        game_id="game_001",
    )

    print(f"結果: {game_record.result}")
    print(f"手数: {game_record.num_moves}")

    # クリーンアップ
    await engine1.close()
    await engine2.close()

asyncio.run(run_single_game())
```

### 異なる持ち時間での対局

```python
game_record = await runner.run_game(
    black_engine=black,
    white_engine=white,
    black_time_control_limits=TimeControlLimits(time_ms=300000, byoyomi_ms=10000),
    white_time_control_limits=TimeControlLimits(time_ms=60000, byoyomi_ms=1000),
)
```

## 関連ドキュメント

- [Engines API](engines.md) - エンジン操作
- [Orchestrators API](orchestrators.md) - 並列実行
