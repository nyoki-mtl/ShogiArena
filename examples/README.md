# Shogi Arena Examples

このディレクトリは Shogi Arena の設定テンプレートと実行例をまとめる場所です。

## ディレクトリ構成

```
examples/configs/
├── run/                              # 実行設定テンプレート
│   ├── example.yaml                  # トーナメント実行（ラウンドロビン、ガントレット）
│   ├── sprt/example.yaml             # SPRT テスト（統計的検定）
│   └── spsa/example.yaml             # SPSA チューニング（パラメータ最適化）
├── resources/
│   ├── engines/                      # エンジン設定テンプレート
│   ├── evals/                        # 評価関数設定
│   └── instances/                    # インスタンス設定（ローカル、SSH）
├── arena_full_reference.yaml         # TournamentRunConfig の全項目リファレンス
└── example.yaml                      # 簡易版サンプル
```

## 使い方

### 1. そのまま実行（テスト用）

```bash
# トーナメント実行
shogiarena run tournament examples/configs/example.yaml

# SPRT テスト
shogiarena run sprt examples/configs/run/sprt/example.yaml

# SPSA チューニング
shogiarena run spsa examples/configs/run/spsa/example.yaml
```

**注意**: サンプル設定はプレースホルダー（`{output_dir}`, `{engine_dir}`）を使用する場合があります。
- `shogiarena init`（`shogiarena config init` の互換エイリアス）を実行してパスを設定するか
- プレースホルダーを絶対パスに置き換えてください

### 2. カスタマイズして使用

```bash
# テンプレートを作業ディレクトリにコピー
cp examples/configs/run/tournament/example.yaml my_tournament.yaml

# 必要な項目を編集（エンジンパス、時間制御など）
vim my_tournament.yaml

# 実行
shogiarena run tournament my_tournament.yaml
```

## 設定ファイルの説明

### Tournament (トーナメント)

`examples/configs/example.yaml` - 総当たり戦やガントレット形式での包括的なエンジン比較。詳細なコメント付きで全オプションを説明。

**主な設定項目:**
- `engines`: 対局させるエンジン（artifact 参照または engine_path）
- `tournament.scheduler`: `round_robin`, `gauntlet`, `swiss` など
- `rules.time_control`: 持ち時間、秒読み、ノード制限など
- `rules.adjudication`: 投了判定、引き分け判定
- `instances`: ローカル実行またはリモート SSH 実行

### SPRT (統計的検定)

`examples/configs/run/sprt/example.yaml` - 2 つのエンジンバージョン間の統計的有意差を早期停止機能付きで検証。

**主な設定項目:**
- `engines`: 2 つのエンジン（baseline と modified）
- `sprt.elo0`: H0 仮説の Elo 差（通常 0.0）
- `sprt.elo1`: H1 仮説の Elo 差（例: 5.0）
- `sprt.alpha`: 第一種過誤率（既定: 0.05）
- `sprt.beta`: 第二種過誤率（既定: 0.05）
- `sprt.max_games`: 最大対局数

**結果の解釈:**
- H0 棄却 → Modified が統計的に有意に強い
- H0 受容 → 有意な差はない
- max_games 到達 → 結論出ず（より多くの対局が必要）

### SPSA (パラメータチューニング)

`examples/configs/run/spsa/example.yaml` - エンジンパラメータの自動最適化。勾配ベースの確率的探索で最適値を発見。

**主な設定項目:**
- `engines`: 1 つのエンジン（baseline と tuned で共用）
- `spsa.parameters_path`: チューニング対象パラメータ定義ファイル
- `spsa.num_updates`: 更新回数
- `spsa.num_parallel`: 並列対局数
- `rules.initial_positions`: 開始局面ファイル（必須）
- `rules.time_control.node_limit`: ノード数制限（推奨）

## リファレンス設定

### arena_full_reference.yaml

`TournamentRunConfig` の全項目を網羅したリファレンス。自分の環境に合わせてカスタマイズして使用。

## 追加リソース

- **詳細ドキュメント**: [https://nyoki-mtl.github.io/ShogiArena/](https://nyoki-mtl.github.io/ShogiArena/)
- **トーナメントガイド**: [User Guide - Tournaments](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments/)
- **SPSA ガイド**: [User Guide - SPSA](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa/)
