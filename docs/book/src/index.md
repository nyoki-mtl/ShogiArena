# ShogiArena ドキュメント

ShogiArena は、USI 将棋エンジンの自動対局、トーナメント管理、SPRT 検定、SPSA チューニングを実行するためのプラットフォームです。

## 主な用途

- 複数エンジンの `round_robin` / `gauntlet` トーナメントを実行する
- 2 エンジンの強さ差を GSPRT / SPRT で検定する
- SPSA でエンジンパラメータを自動調整する
- 実行中の対局、棋譜、順位表、Book 利用状況、チューニング状況をダッシュボードで監視する
- エンジン内蔵定跡の preflight、provenance、remote 配布を扱う
- Python から USI エンジン操作やトーナメント実行を自動化する

## 最短ルート

```bash
pip install shogiarena
shogiarena config init
cp examples/configs/run/tournament/example.yaml tournament.yaml
# tournament.yaml の engines を自分の環境に合わせて編集
shogiarena run tournament tournament.yaml --dry-run
```

実際に動かすには USI エンジン設定が必要です。初めての場合は [クイックスタート](getting-started/quick-start.md) から進めてください。

## 公開 API

利用者向けに互換性を意識している import は次のモジュールです。

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

`shogiarena._core` 配下は内部実装です。ドキュメント内で開発者向けに触れることはありますが、通常の利用では直接 import しないでください。

## 読み方

- 初めて使う: [インストール](getting-started/installation.md) → [クイックスタート](getting-started/quick-start.md)
- 設定を書く: [エンジン設定](user-guide/engine-configuration.md) と [トーナメント](user-guide/tournaments.md)
- 実行を監視する: [ダッシュボード](user-guide/dashboard.md)
- 自動化する: [Python ライブラリ](user-guide/python-library.md)
- コマンドを確認する: [CLI](api/cli.md)
- 統計の背景を知る: [内部技術](internals/index.md)

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
