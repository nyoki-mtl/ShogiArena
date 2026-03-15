# Shogi Arena ドキュメント

**Shogi Arena** は、将棋エンジンの対局・評価・チューニングを行うための高性能なプラットフォームです。

## 主な機能

### トーナメント実行モード

- **Tournament** (`shogiarena run tournament`): ラウンドロビン、ガントレット方式による包括的なエンジン比較
- **SPRT** (`shogiarena run sprt`): 統計的仮説検定による効率的なバージョン比較（早期停止機能付き）
- **SPSA** (`shogiarena run spsa`): 勾配ベースのエンジンパラメータ最適化

### その他の機能

- **リアルタイムダッシュボード**: Web ブラウザでの対局監視、Live View によるリアルタイム更新
- **USI プロトコル完全準拠**: 任意の USI エンジンをサポート
- **Python ライブラリ**: `shogiarena.engine` / `shogiarena.tournament` を使った自動化
- **リモート実行**: SSH 経由での分散トーナメント実行

## 公開 API と内部実装

利用者向けの公開 import は次を基準にしています。

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

実装本体は `shogiarena._core` にあります。これは内部実装であり、import できても公開 API ではありません。

## はじめに

### インストールと初期設定

```bash
# インストール（pip または uv）
pip install shogiarena
# または
uv pip install shogiarena

# 環境の初期化（対話的）
shogiarena config init
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
- **[Dashboard](user-guide/dashboard.md)**: リアルタイムダッシュボードの使い方

### 設定ガイド

- **[Engine Configuration](user-guide/engine-configuration.md)**: エンジン設定ファイルの詳細
- **[Configuration System](user-guide/configuration.md)**: 環境設定とプレースホルダー
- **[Build System](user-guide/build-system.md)**: アーティファクト参照とビルド管理
- **[Opening Books](user-guide/opening-books.md)**: 開局集の作成と管理

### 高度な機能

- **[Remote Execution](user-guide/remote-execution.md)**: SSH 経由のリモート実行
- **[Utility Tools](user-guide/tools.md)**: 追加ツール（mate, generate など）

## 技術ドキュメント

開発者やアーキテクチャに興味がある方向け：

- **[Architecture](technical/architecture.md)**: システム全体のアーキテクチャ
- **[Project Structure](development/project-structure.md)**: ディレクトリ構成とモジュール詳細
- **[Engine Layers](technical/engine-layers.md)**: `shogiarena.engine` と `_core` の関係
- **[USI Engine](technical/usi-engine.md)**: USI プロトコル実装の詳細
- **[Services](technical/services.md)**: Rating, SPRT, Statistics などのサービス
- **[API Reference](api/index.md)**: Python API リファレンス

## その他

- **[Troubleshooting](troubleshooting.md)**: よくある問題と解決方法

## 開発への参加

- **[Contributing](development/contributing.md)**: 開発への参加方法（ビルド、テスト、コーディング規約）

## クイックリファレンス

### 基本コマンド

```bash
# 環境初期化
shogiarena config init

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
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=5_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

```python
import asyncio

from shogiarena.tournament import run_tournament


asyncio.run(run_tournament("tournament.yaml", run_dir="runs/example"))
```

詳細は [Python Library Guide](user-guide/python-library.md) を参照してください。
