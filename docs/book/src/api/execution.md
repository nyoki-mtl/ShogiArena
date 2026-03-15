# Execution API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。利用者向けに公開しているのは主に `shogiarena.engine` と `shogiarena.tournament` です。ここで扱う `GameRunner` などは `_core` の低レベル実装です。

execution レイヤーは「1 局をどう指し切るか」を担当します。トーナメント scheduling や SPRT 判定とは独立して、2 つの engine participant と持ち時間設定から `GameRecord` を組み立てます。

## 主な型

```python
from shogiarena._core.contexts.match.application.engine_participant import (
    EngineParticipant,
)
from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort
from shogiarena._core.contexts.match.ports.usi_think_ports import (
    PonderHitTimings,
    UsiThinkRequest,
)
```

## `GameRunner`

[`GameRunner`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/match/application/runner.py) は 1 局の façade です。実処理は mixin に分かれていますが、呼び出し側から見える入口は `run_game()` です。

### コンストラクタ

- `progress_queue`
  進捗イベント送信用 `asyncio.Queue`
- `time_control_limits`
  両対局者に共通の default 持ち時間
- `adjudication_config`
  最大手数や投了判定
- `repetition_occurrences_to_draw`
  千日手扱いにする出現回数。2 以上必須
- `on_engine_options`
  USI option snapshot を受ける callback

### `run_game()`

```python
await runner.run_game(
    black_engine,
    white_engine,
    initial_sfen="startpos",
    game_id=None,
    black_time_control_limits=None,
    white_time_control_limits=None,
)
```

動作の流れ:

1. 初期局面を正規化
2. 先手/後手の `GameClock` を初期化
3. 両 engine を ready 状態へそろえる
4. 指し手ループを進める
5. 必要なら adjudication と repetition を判定
6. `gameover` を通知して `rshogi.record.GameRecord` を返す

注意点:

- 持ち時間は両 side で必須です
- shutdown 中は例外を抑えて `PAUSED` 扱いに寄せる経路があります
- 返り値は DB や dashboard で再利用される `GameRecord` です

## `EngineParticipant`

[`EngineParticipant`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/match/application/engine_participant.py) は `AsyncUsiEngine` 相当の runtime を `GameEnginePort` に適合させる wrapper です。

責務:

- `start` / `isready` / `usinewgame` / `position` の段取り
- `think` / `think_mate` / `analyze`
- `ponder` 制御
- `gameover` 通知
- engine info / USI option snapshot の取得

重要メソッド:

- `prepare(initial_sfen=...)`
- `prepare_ready_state()`
- `prepare_new_game_position(initial_sfen=...)`
- `think(...)`
- `start_ponder(...)`
- `ponder_hit(...)`
- `cancel_ponder(...)`
- `notify_gameover(result)`
- `shutdown()`

## `GameEnginePort`

[`GameEnginePort`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/match/ports/game_engine_ports.py) は `GameRunner` が engine に要求する最小 protocol です。`EngineParticipant` はこの protocol の標準実装で、テストでは fake 実装もここに合わせます。

必要な能力は次の通りです。

- 対局準備
- 思考開始と停止
- ponder 制御
- gameover 通知
- snapshot 提供
- I/O log handler 登録

## `UsiThinkRequest` と `PonderHitTimings`

[`UsiThinkRequest`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/match/ports/usi_think_ports.py) は USI `go` コマンドの構造化表現です。

主なフィールド:

- `movetime`
- `btime`, `wtime`
- `binc`, `winc`
- `byoyomi`
- `depth`
- `nodes`
- `is_infinite`
- `is_ponder`
- `searchmoves`

補助 API:

- `to_command()`
- `request_from_time_controls(...)`

`PonderHitTimings` は `ponderhit` 時に追加送信する時計情報の構造化 payload です。

## 使用例

```python
import asyncio

from shogiarena.engine import create_engine
from shogiarena._core.contexts.match.application.engine_participant import (
    EngineParticipant,
)
from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


async def main() -> None:
    engine = await create_engine(".sandbox/configs/engines/example.yaml")
    participant = EngineParticipant(engine)
    runner = GameRunner(time_control_limits=TimeControlLimits(time_ms=1000, byoyomi_ms=0))
    await participant.prepare(initial_sfen="startpos")
    result = await participant.think(
        sfen="startpos",
        moves=(),
        request=UsiThinkRequest(movetime=1000),
    )
    print(result.bestmove)
    await participant.shutdown()


asyncio.run(main())
```

## 設計メモ

- execution レイヤーは tournament ルール全体ではなく「1 局」を担当します
- `GameRunner` は facade を薄く保ち、内部は mixin に分割しています
- `GameEnginePort` を使うことで、USI runtime と対局進行を疎結合にしています
