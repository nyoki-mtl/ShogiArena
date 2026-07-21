# Shogi Arena Examples

このディレクトリは Shogi Arena の設定テンプレートと実行例をまとめる場所です。

## ディレクトリ構成

```
examples/configs/
├── run/                              # 実行設定テンプレート
│   ├── tournament/example.yaml       # トーナメント実行（ラウンドロビン、ガントレット）
│   ├── sprt/example.yaml             # SPRT テスト（統計的検定）
│   ├── spsa/example.yaml             # SPSA チューニング（パラメータ最適化）
│   └── generate/example.yaml         # 自己対局による棋譜生成
├── resources/
│   ├── engines/                      # エンジン設定テンプレート
│   ├── instances/                    # インスタンス設定（ローカル、SSH）
│   └── spsa/                         # SPSA space 定義
├── arena_full_reference.yaml         # TournamentRunConfig の全項目リファレンス
└── example.yaml                      # 簡易版サンプル
```

## 使い方

### 1. テンプレートをコピーして調整

```bash
# トーナメント実行
cp examples/configs/run/tournament/example.yaml my_tournament.yaml

# SPRT テスト
cp examples/configs/run/sprt/example.yaml my_sprt.yaml

# SPSA チューニング
cp examples/configs/run/spsa/example.yaml my_spsa.yaml
```

**注意**：サンプル設定は artifact 参照やプレースホルダー（`{output_dir}`、`{engine_dir}`）を使う場合があります。
次のどちらかで実環境のパスに解決してください。

- `shogiarena config init` を実行してパスを設定する
- エンジン、インスタンス、評価関数、定跡などのパスを自分の環境に合わせて書き換える

### 2. 検証して実行

```bash
shogiarena run tournament my_tournament.yaml --dry-run
shogiarena run tournament my_tournament.yaml
```

## 設定ファイルの説明

### Tournament (トーナメント)

`examples/configs/run/tournament/example.yaml` は、現行仕様に合わせたトーナメント実行テンプレートです。

`examples/configs/example.yaml` は、総当たり戦やガントレット形式でエンジンをまとめて比較する例です。
主要オプションに詳細なコメントを付けています。

主な設定項目：

- `engines`：対局させるエンジン（artifact 参照または engine_path）
- `tournament.scheduler`：`round_robin` または `gauntlet`
- `rules.time_control`：持ち時間、秒読み、ノード制限など
- `rules.adjudication`：投了判定、引き分け判定
- `instances`：ローカル実行またはリモート SSH 実行

### SPRT (統計的検定)

`examples/configs/run/sprt/example.yaml` は、2 つのエンジンバージョンの差を早期停止付きで検定する例です。

主な設定項目：

- `engines`：2 つのエンジン（baseline と modified）
- `sprt.elo0`：H0 仮説の Elo 差（通常 0.0）
- `sprt.elo1`：H1 仮説の Elo 差（例: 5.0）
- `sprt.alpha`：第一種過誤率（既定: 0.05）
- `sprt.beta`：第二種過誤率（既定: 0.05）
- `sprt.max_games`：最大対局数

結果の解釈：

- H0 棄却 → modified 側が統計的に有意に強い
- H0 受容 → 設定した閾値を超える差は認められない
- max_games 到達 → 結論は出ない（対局数を増やす必要がある）

### SPSA (パラメータチューニング)

`examples/configs/run/spsa/example.yaml` は、エンジンパラメータを自動調整する例です。
勾配を近似する確率的探索によって、良い値を探索します。

主な設定項目：

- `engines`：1 つのエンジン（baseline と tuned で共用）
- `spsa.space`：チューニング対象パラメータの SPSA space 定義ファイル
- `spsa.num_updates`：更新回数
- `spsa.num_parallel`：並列対局数
- `rules.initial_positions`：開始局面ファイル（必須）
- `rules.time_control.node_limit`：ノード数制限（推奨）

### Generate (棋譜生成)

`examples/configs/run/generate/example.yaml` は、自己対局で棋譜と局面データを生成する例です。

主な設定項目：

- `engines`：自己対局に使うエンジン定義
- `tournament.games_per_pair`：生成する対局数
- `rules`：持ち時間、初期局面、adjudication
- `records_output`：sbinpack や psv 形式の出力設定

## リファレンス設定

### arena_full_reference.yaml

`TournamentRunConfig` の全項目を網羅したリファレンスです。
自分の環境に合わせて必要な項目だけを書き換えて使います。

## 追加リソース

- **詳細ドキュメント**：[https://nyoki-mtl.github.io/ShogiArena/](https://nyoki-mtl.github.io/ShogiArena/)
- **トーナメントガイド**：[User Guide - Tournaments](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments.html)
- **SPSA ガイド**：[User Guide - SPSA](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa.html)
