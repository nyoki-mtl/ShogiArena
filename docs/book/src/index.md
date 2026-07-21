# ShogiArena ドキュメント

ShogiArena は、USI 将棋エンジンの自動対局、トーナメント管理、SPRT 検定、SPSA チューニングを実行するためのプラットフォームです。

## 主な用途

- 複数エンジンの `round_robin` / `gauntlet` トーナメントを実行する
- 2 エンジンの強さ差を GSPRT / SPRT で検定する
- SPSA でエンジンパラメータを自動調整する
- 実行中の対局、棋譜、順位表、Book 利用状況、チューニング状況をダッシュボードで監視する
- エンジン内蔵定跡の preflight、provenance、remote 配布を扱う
- Python から USI エンジン操作やトーナメント実行を自動化する

## インストールから dry-run まで

```bash
pip install shogiarena
shogiarena config init
# tournament.yaml を用意（雛形は下記リポジトリの examples/ にあります）
shogiarena run tournament tournament.yaml --dry-run
```

設定の雛形は[リポジトリの examples ディレクトリ](https://github.com/nyoki-mtl/ShogiArena/tree/main/examples/configs)にあります。
最小構成は[クイックスタート](getting-started/quick-start.md)に載せています。

実際に対局を動かすには USI エンジンの設定ファイルが必要です。
初めて使う場合は [クイックスタート](getting-started/quick-start.md) から進めてください。

## 公開 API

利用者向けに後方互換性を保つよう努めているモジュールは次の 4 つです。

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

`shogiarena._core` 配下は内部実装です。
本ドキュメントでも開発者向けの説明では登場しますが、通常の利用で直接 import することは想定していません。

## 目的別の読み進め方

- **初めて使う**：[インストール](getting-started/installation.md) → [クイックスタート](getting-started/quick-start.md)
- **設定を書く**：[エンジン設定](user-guide/engine-configuration.md) と [トーナメント](user-guide/tournaments.md)
- **実行を監視する**：[ダッシュボード](user-guide/dashboard.md)
- **自動化する**：[Python ライブラリ](user-guide/python-library.md)
- **コマンドを確認する**：[CLI](api/cli.md)
- **統計の背景を知る**：[内部技術](internals/index.md)

## 基本コマンド

```bash
# トーナメント
shogiarena run tournament config.yaml

# SPRT
shogiarena run sprt config.yaml

# SPSA
shogiarena run spsa config.yaml

# 自己対局による棋譜生成
shogiarena run generate config.yaml

# 保存済み run のダッシュボード表示
shogiarena dashboard serve --run-dir /path/to/run

# 結果集計
shogiarena results summary /path/to/run --format text
```
