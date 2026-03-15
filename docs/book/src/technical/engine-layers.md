# エンジンラッパーのレイヤー設計

現在の ShogiArena では、利用者向けの入口は `shogiarena.engine` に寄せています。内部実装は `_core` にあります。

## レイヤー構成

```text
1. shogiarena.engine
   利用者向け facade

2. _core.platform.engine_runtime.AsyncUsiEngine
   USI セッション管理

3. _core.platform.engine_runtime.AsyncUsiProcess
   プロセス状態管理

4. _core.platform.engine_provisioning.SpawnerBackedUSIBridge
   ローカル / SSH 起動と I/O

5. process spawner / host probe / settings
   実行基盤
```

## Layer 1: `shogiarena.engine`

役割:

- 安定した import パスを提供する
- `create_engine()` / `create_engine_from_mapping()` を公開する
- `_core` の置き場所を隠す

この層は薄く保ちます。重い実装や依存配線の正本は持ちません。

## Layer 2: `AsyncUsiEngine`

役割:

- `usi` / `isready` / `usinewgame` の制御
- `think()` / `think_mate()` / `analyze()` / `stop()`
- `info` 行のパースと状態遷移

ここが実質的な USI セッション管理の中核です。

## Layer 3: `AsyncUsiProcess`

役割:

- bridge の start / stop を包む
- 実行中かどうかの状態を管理する
- I/O を使う前提条件を揃える

プロトコル知識は持たず、USI の意味論は Layer 2 に任せます。

## Layer 4: `SpawnerBackedUSIBridge`

役割:

- プロセスの実起動
- stdout / stdin / stderr の接続
- ローカル実行と SSH 実行の抽象化

`AsyncUsiEngine` は「USI を話す」ことに集中し、プロセス起動の詳細はここに押し込みます。

## Layer 5: 実行基盤

役割:

- engine process spawner
- artifact 解決
- remote path 書換
- settings / host probe

これは engine API そのものではなく、engine を作る土台です。

## なぜこの分離にしているか

- 公開 import を短く保つため
- `_core` を自由に整理しやすくするため
- USI 制御、プロセス制御、実行基盤を混ぜないため
- テストをレイヤ単位で書きやすくするため

## 以前との違い

- `SyncUsiEngine` は現時点で正式公開していません
- `EngineFactory` ではなく `create_engine()` が公開入口です
- deep import より `shogiarena.engine` を優先します

詳細な振る舞いは [USI Engine](usi-engine.md) を参照してください。
