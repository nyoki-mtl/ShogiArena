# アーキテクチャ概要

Shogi Arena は、拡張性と堅牢性を重視した階層型アーキテクチャを採用しています。

## 全体像

現在の ShogiArena は、利用者向け facade と `_core` 実装を分離した構成です。

```text
user code / CLI
    |
    +-- shogiarena.engine / tournament / cli / composition
            |
            +-- shogiarena._core.interfaces
            +-- shogiarena._core.contexts
            +-- shogiarena._core.platform
            +-- shogiarena._core.shared.kernel
```

## 1. Public facade

利用者向けの入口は `src/shogiarena/*.py` に限定します。

- `shogiarena.engine`: USI エンジン操作
- `shogiarena.tournament`: tournament / sprt 実行
- `shogiarena.cli`: CLI entrypoint
- `shogiarena.composition`: advanced wiring

この層は薄く保ち、実装の正本は持ちません。

## 2. _core.interfaces

CLI、dashboard、boundary parser などの入出力境界です。

- CLI: 設定ファイル読込、引数解釈、コマンド実行
- Dashboard: API サーバ、静的アセット、frontend
- Boundaries: parse / serialize

## 3. _core.contexts

業務ルールを context 単位で持ちます。

- `match`: 1 局の対局実行
- `game_session`: 共通的な run/session orchestration
- `tournament`: tournament / sprt 実行
- `spsa`: SPSA 実行
- `instances`: engine runtime 生成と instance 管理
- `engine_catalog`: エンジンメタデータとカタログ管理
- `dashboard`: dashboard 用の表示・集約ロジック

各 context は `domain / application / ports / adapters` に分かれます。

## 4. _core.platform

共通 I/O と実行基盤です。

- engine provisioning / runtime
- database / records
- host probe / settings

## 5. _core.shared.kernel

最小共通核です。

- 型
- JSON / scalar coercion
- serialization
- service ports

ここには context 固有の業務ロジックを置きません。

## 主要なデータフロー

### Python から engine を使う場合

1. `shogiarena.engine.create_engine()` を呼ぶ
2. facade が default composition root から `engine_runtime` を取得する
3. `_core.platform.engine_runtime` が `AsyncUsiEngine` と bridge を組み立てる
4. 利用者は `AsyncUsiEngine` を使って `think()` / `analyze()` する

### Python から tournament を使う場合

1. `shogiarena.tournament.run_tournament()` または `load_tournament_config()` を呼ぶ
2. facade が default composition root から `tournament_runtime` を取得する
3. `_core.contexts.tournament` が config を構築し `TournamentRunner` を実行する
4. 実行結果や進捗は storage / dashboard / records に流れる

## 非同期処理のデザイン

Shogi Arena は、多数のエンジンプロセスを効率的に管理するために `asyncio` を全面的に採用しています。

- エンジンの入出力監視（stdout/stdin）をノンブロッキングで実施。
- SSH 経由のリモート実行も `asyncssh` により統合。
- 同時対局数はセマフォによって制御され、CPU リソースを最適に配分します。

## 補足

- `shogiarena._core.*` は内部実装です
- deep import は contributor 向けで、外部利用者に対しては互換保証しません
- 公開面の整理は `engine` / `tournament` を優先しています
