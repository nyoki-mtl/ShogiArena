# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/docs.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/docs.yml)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

> [!NOTE]
> **本プロジェクトは開発中です。** 開発者の都合により、API・設定・実装などが予告なく大きく変更される場合があります。破壊的変更については [CHANGELOG](CHANGELOG.md) を参照してください。

**📖 ドキュメント:** [https://nyoki-mtl.github.io/ShogiArena/](https://nyoki-mtl.github.io/ShogiArena/)  
**📄 English README:** [README.md](README.md)

---

**ShogiArena** は将棋エンジン開発と評価のための総合プラットフォームです。以下の機能を提供します：

- **将棋エンジンの Python ラッパー**: USI 対応の任意の将棋エンジンを Python プロジェクトに簡単に統合できる同期・非同期インターフェース（`SyncUsiEngine`, `AsyncUsiEngine`）
- **高度な対戦環境**: カスタマイズ可能なルール、時間制御、判定機能、並列対局実行による柔軟なトーナメント実行（ラウンドロビン、SPRT、SPSA）
- **リアルタイムダッシュボード**: 対局のライブ監視、統計分析、レーティング推移の可視化を行う Web ベースのインターフェース
- **エンジンパラメータチューニング**: SPSA による勾配ベースの自動最適化と SPRT による統計的検証

## デモ

https://github.com/user-attachments/assets/1cdebe23-b1a9-4d8e-91c0-f56ca970b569

*リアルタイム更新とインタラクティブなダッシュボードによるトーナメント監視*

## インストール

```bash
pip install shogiarena
```

開発者向けのインストール方法は [DEVELOPMENT.md](DEVELOPMENT.md) を参照してください。

## 設定（オプション）

必須ではありませんが、以下の場合は `shogiarena init`（`shogiarena config init` の互換エイリアス）の実行を推奨します：

```bash
shogiarena init
```

**設定される内容：**
- **出力ディレクトリ**: トーナメント結果とデータベースの一元管理場所
- **エンジンキャッシュ**: プロジェクト横断で共有するエンジンバイナリの保存先
- **リポジトリ統合**: artifact 参照によるエンジンリポジトリ（YaneuraOu など）へのアクセス
- **プレースホルダー対応**: 設定ファイル内で `{output_dir}` や `{engine_dir}` を使用可能に

**設定なしでもできること：**
- ✅ Python API（`SyncUsiEngine`, `AsyncUsiEngine`）を絶対パスで使用
- ✅ 設定ファイルに絶対パスを書いてトーナメント実行
- ✅ デフォルトパス（`./shogiarena_output`、一時ディレクトリ）で全機能が動作

**設定が必要なこと：**
- ❌ artifact 参照（例: `artifact: yaneuraou@main:YaneuraOu-by-gcc`）
- ❌ 設定ファイル内のプレースホルダー変数（例: `path: "{engine_dir}/myengine"`）
- ❌ プライベート GitHub リポジトリへのアクセス（`github_token` の設定が必要）

詳細は [セットアップガイド](https://nyoki-mtl.github.io/ShogiArena/getting-started/quick-start/) を参照してください。

## クイック例

### 例1: Python でエンジンを使う

USI エンジンを Python コードに統合する最もシンプルな方法：

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# 任意の USI エンジンを使用
with SyncUsiEngine.from_config_path("engine.yaml") as engine:
    request = UsiThinkRequest(time_ms=5000)
    result = engine.think(sfen="startpos", request=request)
    print(f"Bestmove: {result.bestmove}, Score: {result.score_cp}cp")
```

または直接パスを指定：

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine

with SyncUsiEngine.from_path("/path/to/engine") as engine:
    result = engine.think(sfen="startpos", request=UsiThinkRequest(nodes=1000000))
    print(result.bestmove)
```

### 例2: CLI でトーナメント実行

提供されているサンプル設定を使用：

```bash
# トーナメント（ラウンドロビン）
shogiarena run tournament examples/configs/run/tournament/example.yaml

# SPRT（統計的検定）
shogiarena run sprt examples/configs/run/sprt/example.yaml

# SPSA（パラメータチューニング）
shogiarena run spsa examples/configs/run/spsa/example.yaml
```

ダッシュボードが自動的に `http://localhost:8080` で起動し、進行状況を監視できます。

**注意**: サンプル設定は `{output_dir}` / `{engine_dir}` などのプレースホルダーを使用する場合があります。`shogiarena init`（`shogiarena config init` の互換エイリアス）を実行して設定するか、絶対パスに置き換えてください。詳細は [サンプル設定](#サンプル設定) を参照してください。

## 主な機能

### トーナメントモード

**ラウンドロビントーナメント**
```bash
shogiarena run tournament examples/configs/run/tournament/example.yaml
```
カスタマイズ可能な時間制御、開始局面、判定ルールによる包括的なエンジン比較。

**SPRT（逐次確率比検定）**
```bash
shogiarena run sprt examples/configs/run/sprt/example.yaml
```
統計的早期停止機能により、エンジンバージョンの強さの差を効率的に検定。

**SPSA（同時摂動確率近似法）**
```bash
shogiarena run spsa examples/configs/run/spsa/example.yaml
```
並列対局実行による勾配ベースの確率的探索でエンジンパラメータを最適化。

詳細は [トーナメントガイド](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments/) と [SPSA ガイド](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa/) を参照してください。

### ダッシュボード機能

- **ライブ更新**: リアルタイムの対局進行と統計
- **インタラクティブな可視化**: レーティング推移、勝率マトリックス、パラメータ収束
- **対局ブラウザ**: 個別対局の再生と解析
- **SPSA トラッキング**: パラメータ値と勾配推定の監視

### Python ライブラリ

シンプルなエンジン操作を超えた高度な機能も提供：

**非同期エンジン制御**
```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def compare_engines():
    # 複数エンジンを並行作成
    engine1 = await EngineFactory.create_engine("engine1.yaml")
    engine2 = await EngineFactory.create_engine("engine2.yaml")
    await asyncio.gather(engine1.start(), engine2.start())
    
    # 同一局面を並行解析
    results = await asyncio.gather(
        engine1.think(sfen="startpos", request=UsiThinkRequest(time_ms=5000)),
        engine2.think(sfen="startpos", request=UsiThinkRequest(time_ms=5000)),
    )
```

**プログラムによるトーナメント実行**
```python
from pathlib import Path

from shogiarena.arena.configs.tournament import ArenaConfig
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import FilesystemRunStorage

# 完全な制御でプログラムからトーナメントを実行
config = ArenaConfig.from_yaml("tournament.yaml")
storage = FilesystemRunStorage(Path("runs/tournament"))
runner = TournamentRunner(config, storage=storage)
runner.run_sync()  # または非同期: await runner.run()
```

**カスタム解析ツール**
```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

# カスタム解析ワークフローを構築
with SyncUsiEngine.from_path("/path/to/engine") as engine:
    for position in my_position_list:
        result = engine.think(sfen=position, request=UsiThinkRequest(nodes=1000000))
        analyze_and_store(result)
```

詳細な API ドキュメントは [Python ライブラリガイド](https://nyoki-mtl.github.io/ShogiArena/user-guide/python-library/) を参照してください。

## ドキュメント

- **[はじめに](https://nyoki-mtl.github.io/ShogiArena/getting-started/)** - インストールと最初のステップ
- **[ユーザーガイド](https://nyoki-mtl.github.io/ShogiArena/user-guide/)** - トーナメント設定、SPSA チューニング、Python API
- **[技術ドキュメント](https://nyoki-mtl.github.io/ShogiArena/technical/)** - アーキテクチャ、USI プロトコル、サービス
- **[API リファレンス](https://nyoki-mtl.github.io/ShogiArena/api/)** - コアクラスとモジュール
- **[開発ガイド](DEVELOPMENT.md)** - コントリビューション、ビルド、テスト

## サンプル設定

[`examples/configs/`](examples/configs/) ディレクトリには、すぐに使える設定テンプレートがあります：

**トーナメント設定:**
- [`examples/configs/run/tournament/example.yaml`](examples/configs/run/tournament/example.yaml) - 詳細なコメント付きの包括的なトーナメント設定
- [`examples/configs/run/sprt/example.yaml`](examples/configs/run/sprt/example.yaml) - SPRT テスト設定
- [`examples/configs/run/spsa/example.yaml`](examples/configs/run/spsa/example.yaml) - SPSA パラメータチューニング設定

**エンジン設定:**
- [`examples/configs/resources/engines/`](examples/configs/resources/engines/) - 各種オプション付きエンジン設定テンプレート

**インスタンス設定:**
- [`examples/configs/resources/instances/local_example.yaml`](examples/configs/resources/instances/local_example.yaml) - ローカル実行
- [`examples/configs/resources/instances/ssh_example.yaml`](examples/configs/resources/instances/ssh_example.yaml) - リモート SSH 実行
- [`examples/configs/resources/instances/ssh_pool_example.yaml`](examples/configs/resources/instances/ssh_pool_example.yaml) - SSH インスタンスプール

**注意**: サンプル設定は `{output_dir}` / `{engine_dir}` などのプレースホルダー変数を使用する場合があります。`shogiarena init`（`shogiarena config init` の互換エイリアス）で設定するか、絶対パスに置き換えてください。

## ライセンス

このプロジェクトは MIT ライセンスの下で公開されています。詳細は [LICENSE](LICENSE) ファイルを参照してください。
