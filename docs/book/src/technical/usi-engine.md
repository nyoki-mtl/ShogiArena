# USI Engine の設計

このページでは、現在の USI engine 実装を public facade から見たときにどう動いているかを説明します。

## 利用者から見た入口

公開入口は `shogiarena.engine` です。

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(byoyomi=1_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

## 中核クラス: `AsyncUsiEngine`

`AsyncUsiEngine` は高レベルの USI セッションを扱います。

責務:

- プロセス起動後の handshake
- `setoption`
- `position`
- `go`
- `go mate`
- `go infinite`
- `stop`
- `quit`

## 典型的なライフサイクル

```text
create_engine()
  -> AsyncUsiEngine を構築
async with engine
  -> start()
     -> process start
     -> usi / usiok
     -> setoption
     -> isready / readyok
  -> think() / analyze() / think_mate()
__aexit__
  -> close()
     -> quit
```

## 状態遷移

概念的には次のように進みます。

```text
WAITING_FOR_USIOK
  -> NOT_READY
  -> WAITING_FOR_READYOK
  -> READY
  -> WAITING_FOR_BESTMOVE / PONDER / WAITING_FOR_PONDER_BESTMOVE / WAITING_FOR_CHECKMATE
  -> READY
  -> WILL_QUIT
  -> QUIT_COMPLETED
```

`AsyncUsiEngine.state` で現在状態を観測できます。

## `think()`

通常の思考です。

```python
result = await engine.think(
    sfen="startpos",
    request=UsiThinkRequest(movetime=1_000),
)
```

内部的には:

1. `position` を送る
2. `go ...` を送る
3. `info` を受ける
4. `bestmove` を待つ
5. `UsiThinkResult` を返す

## `analyze()`

無限解析の入口です。

```python
handle = await engine.analyze(
    sfen="startpos",
    request=UsiThinkRequest(is_infinite=True),
)
result = await handle.stop()
```

## `think_mate()`

`go mate` を使った詰み探索です。

```python
mate = await engine.think_mate(sfen="startpos", ply_limit=31)
```

## `UsiThinkRequest`

`UsiThinkRequest` は `go` コマンドの structured representation です。

代表的なフィールド:

- `movetime`
- `btime`, `wtime`
- `binc`, `winc`
- `byoyomi`
- `depth`
- `nodes`
- `is_infinite`
- `is_ponder`

## 低レベル実装

`AsyncUsiEngine` の下には次がいます。

- `AsyncUsiProcess`: プロセス状態管理
- `SpawnerBackedUSIBridge`: ローカル/SSH を含む実プロセス起動

これらは `_core` の実装詳細です。外部利用者には通常不要です。

## 設計上の方針

- 利用者向け import は短く保つ
- 実装配置は `_core` に閉じ込める
- engine 制御は `asyncio` ベースに統一する
- public facade と内部実装を分離する

## 補足

- `SyncUsiEngine` は現在の正式公開面に含めていません
- engine 生成の入口は `EngineFactory` ではなく `create_engine()` です
- deep import は contributor 向けです
