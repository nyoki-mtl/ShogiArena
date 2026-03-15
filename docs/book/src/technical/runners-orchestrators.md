# Runner と Orchestrator

> [!WARNING]
> このページは内部アーキテクチャの説明です。利用者向けの公開 Python API は `shogiarena.tournament` を基準にしてください。

ShogiArena では、run 全体の責務と 1 局ごとの並列実行責務を分離しています。

## いまの public / internal の切り分け

利用者から見える入口:

- `shogiarena.tournament.run_tournament()`
- `shogiarena.tournament.load_tournament_config()`
- `shogiarena.tournament.build_tournament_runner()`
- `shogiarena.tournament.TournamentRunner`

内部実装:

- `_core.contexts.tournament.*`
- `_core.contexts.game_session.*`
- `_core.contexts.match.*`

## Runner の責務

Runner は「run 全体」を管理します。

具体的には次です。

- 設定の受理
- storage / dashboard / engine factory などの依存の保持
- スケジュール実行の開始
- 中断再開の制御
- 集計と終了処理

公開面では `TournamentRunner` がこの役割を担います。

## Orchestration の責務

Orchestration 側は「複数局の実行フロー」を扱います。

- game schedule の消費
- worker への割当
- engine pool からの取得と返却
- progress event の発行
- completion / summary 更新

この責務は主に `_core.contexts.game_session.application.orchestration` にあります。

## Match 実行の責務

1 局の実行はさらに下位に分離されています。

- `match` context:
  - USI エンジン 2 台を使って 1 局を進める
  - 持ち時間
  - 投了 / 千日手 / 最大手数などの判定

つまり責務の粒度はこうです。

```text
TournamentRunner
  -> session orchestration
    -> match execution
      -> AsyncUsiEngine
```

## `TournamentRunner` と `sprt`

現在の public API では `SprtRunner` を別名で公開していません。

理由は単純で、実行入口としては

- tournament config をロードする
- `TournamentRunner` を作る
- `sprt` セクションがあればその設定込みで走る

という流れで十分だからです。

つまり `sprt` は「別 runner を import する public API」ではなく、「`TournamentRunner` が扱う config variation」です。

## `spsa` との違い

SPSA は現在 CLI-first です。

- CLI では `shogiarena run spsa ...`
- Python 側の formal public surface はまだ確定していない

そのため、このページで `SpsaRunner` という内部概念に触れることはあっても、利用者向け import としては勧めません。

## composition root との関係

public helper は composition root 経由で runtime を取ります。

```text
shogiarena.tournament.run_tournament()
  -> shogiarena.composition.build_default_root()
  -> root.tournament_runtime
  -> TournamentRunner
```

ここで重要なのは、facade 自体は薄く、依存グラフの正本は `_core.interfaces.composition_root.default_root` にあることです。

## contributor 向けの正規参照先

- runner 実装:
  - `_core.contexts.tournament.adapters.runner`
- tournament runtime:
  - `_core.contexts.tournament.adapters.runtime_adapter`
- session orchestration:
  - `_core.contexts.game_session.application.orchestration`
- 1 局実行:
  - `_core.contexts.match.application`

## 実務上の指針

- 外部利用者向け説明では `run_tournament()` を優先する
- advanced API として `TournamentRunner` を出す
- `_core` 側の runner / orchestrator 名をそのまま public API にしない
- 依存注入の正本は composition root に寄せる
