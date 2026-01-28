# Shogi Arena ドキュメント

**Shogi Arena** は、将棋エンジンの対局・評価・チューニングを行うための高性能なプラットフォームです。

## 主な機能

### トーナメント実行モード

- **Tournament** (`shogiarena run tournament`): ラウンドロビン、スイス式、ガントレット方式による包括的なエンジン比較
- **SPRT** (`shogiarena run sprt`): 統計的仮説検定による効率的なバージョン比較（早期停止機能付き）
- **SPSA** (`shogiarena run spsa`): 勾配ベースのエンジンパラメータ最適化

### その他の機能

- **リアルタイムダッシュボード**: Web ブラウザでの対局監視、Live View によるリアルタイム更新
- **USI プロトコル完全準拠**: 任意の USI エンジンをサポート
- **Python ライブラリ**: 独自の対局スクリプトや分析ツールを作成可能（`AsyncUsiEngine`, `SyncUsiEngine`, Runners など）
- **リモート実行**: SSH 経由での分散トーナメント実行

## はじめに

### インストールと初期設定

```bash
# インストール
pip install shogiarena

# 環境の初期化（対話的）
shogiarena init
```

詳細は [インストールガイド](getting-started/installation.md) を参照してください。

### 最初のトーナメント

```bash
# トーナメント実行
shogiarena run tournament my-tournament.yaml
```

ステップバイステップのチュートリアルは [クイックスタート](getting-started/quick-start.md) を参照してください。

## ユーザーガイド

### 主要機能

- **[Tournaments](user-guide/tournaments.md)**: トーナメント設定、スケジューラー、時間制御、判定ルール
- **[SPSA Tuning](user-guide/spsa.md)**: パラメータチューニングの設定と実行
- **[Python Library](user-guide/python-library.md)**: Python API の使用方法（エンジン操作、カスタムランナー）

### その他のガイド

- **[Dashboard](user-guide/dashboard.md)**: リアルタイムダッシュボードの使い方
- **[Utility Tools](user-guide/tools.md)**: 追加ツール（analyze, mate, generate）とリモート実行

## 技術ドキュメント

開発者やアーキテクチャに興味がある方向け：

- **[Architecture](technical/architecture.md)**: システム全体のアーキテクチャ
- **[Project Structure](development/project-structure.md)**: ディレクトリ構成とモジュール詳細
- **[USI Engine](technical/usi-engine.md)**: USI プロトコル実装の詳細
- **[Services](technical/services.md)**: Rating, SPRT, Statistics などのサービス
- **[API Reference](api/index.md)**: Python API リファレンス

## 開発への参加

- **[Contributing](development/contributing.md)**: 開発への参加方法
- **[DEVELOPMENT.md](https://github.com/nyoki-mtl/ShogiArena/blob/main/DEVELOPMENT.md)**: 開発ガイド（ビルド、テスト、コーディング規約）

## クイックリファレンス

### 基本コマンド

```bash
# 環境初期化
shogiarena init

# トーナメント実行
shogiarena run tournament config.yaml

# SPRT テスト
shogiarena run sprt config.yaml

# SPSA チューニング
shogiarena run spsa config.yaml

# ダッシュボードサーブ（完了後の結果確認）
shogiarena dashboard serve --run-dir /path/to/run
```

### Python ライブラリ

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# USI エンジンの使用
with SyncUsiEngine.from_config_path("engine.yaml") as engine:
    request = UsiThinkRequest(time_ms=5000)
    result = engine.think(sfen="startpos", request=request)
    print(f"Bestmove: {result.bestmove}")
```

```python
from shogiarena.arena.configs.tournament import ArenaConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner

# トーナメントの実行
config = ArenaConfig.from_yaml("tournament.yaml")
runner = TournamentRunner(config)
runner.run_sync()
```

詳細は [Python Library Guide](user-guide/python-library.md) を参照してください。
