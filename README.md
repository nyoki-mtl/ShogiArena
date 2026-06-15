# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-docs.yml/badge.svg)](https://nyoki-mtl.github.io/ShogiArena/index.html)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

ShogiArena は、USI 将棋エンジン同士の自動対局、トーナメント管理、SPRT による統計検定、SPSA パラメータチューニングをまとめて扱うための実行基盤です。CLI からすぐ動かせることと、Python API から自動化しやすいことを重視しています。

> [!NOTE]
> 本プロジェクトは開発中です。公開 API は `shogiarena.engine` / `shogiarena.tournament` を中心に整理中で、破壊的変更を含む更新が入ることがあります。変更履歴は [CHANGELOG.md](CHANGELOG.md) を参照してください。

**ドキュメント:** [https://nyoki-mtl.github.io/ShogiArena/](https://nyoki-mtl.github.io/ShogiArena/)

## デモ

https://github.com/user-attachments/assets/1cdebe23-b1a9-4d8e-91c0-f56ca970b569

リアルタイム更新に対応したダッシュボードで、進行中の対局、順位表、棋譜、SPSA の更新状況を確認できます。

## できること

- `round_robin` / `gauntlet` 形式のトーナメント実行
- 2 エンジン間の GSPRT / SPRT 検定と早期停止
- SPSA による USI オプション・評価パラメータのチューニング
- 実行中・完了後の Web ダッシュボード表示
- SFEN / KIF / CSA などの棋譜保存と結果集計
- エンジン内蔵定跡の検証、provenance 記録、Book タブでの out-of-book 分析
- ローカル実行と SSH インスタンスを使った分散実行
- `shogiarena.engine` / `shogiarena.tournament` による Python からの自動化

## インストール

Python 3.11 以上が必要です。

```bash
pip install shogiarena
```

ソースから開発する場合は `uv` を使います。

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync --all-extras
uv run shogiarena --help
```

必要に応じて標準の出力先やエンジン配置先を初期化します。

```bash
shogiarena config init
```

この設定は `{output_dir}` / `{engine_dir}` プレースホルダー、artifact ベースのエンジン解決、共有キャッシュを使う場合に便利です。

## クイックスタート

エンジン設定を 2 つ用意します。

```yaml
# engine_a.yaml
name: "EngineA"
engine_path: "/path/to/engine_a"
options:
  Threads: 2
  USI_Hash: 256
```

```yaml
# engine_b.yaml
name: "EngineB"
engine_path: "/path/to/engine_b"
options:
  Threads: 2
  USI_Hash: 256
```

トーナメント設定を作成します。

```yaml
# tournament.yaml
experiment_name: "my_first_tournament"

engines:
  - engine_path: "engine_a.yaml"
  - engine_path: "engine_b.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 10
  num_parallel: 2

rules:
  time_control:
    time_ms: 10000
    increment_ms: 100

dashboard:
  enabled: true
  api_port: 8080
```

まず検証し、問題なければ実行します。

```bash
shogiarena run tournament tournament.yaml --dry-run
shogiarena run tournament tournament.yaml
```

`dashboard.enabled: true` の場合は `http://localhost:8080` でダッシュボードを開けます。

## よく使うコマンド

```bash
# トーナメント（テンプレートを編集してから実行）
cp examples/configs/run/tournament/example.yaml tournament.yaml
shogiarena run tournament tournament.yaml --dry-run

# SPRT（テンプレートを編集してから実行）
cp examples/configs/run/sprt/example.yaml sprt.yaml
shogiarena run sprt sprt.yaml --dry-run

# SPSA（テンプレートを編集してから実行）
cp examples/configs/run/spsa/example.yaml spsa.yaml
shogiarena run spsa spsa.yaml --dry-run

# 自己対局による棋譜生成（テンプレートを編集してから実行）
cp examples/configs/run/generate/example.yaml generate.yaml
shogiarena run generate generate.yaml --dry-run

# 保存済み run のダッシュボード表示
shogiarena dashboard serve --run-dir /path/to/run

# 結果集計
shogiarena results summary /path/to/run --format text
```

## Python API

正式な公開入口は次のモジュールです。

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

`shogiarena._core` 配下は内部実装です。import できても後方互換性は保証されません。

### USI エンジンを使う

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

### トーナメントを実行する

```python
import asyncio

from shogiarena.tournament import run_tournament


async def main() -> None:
    await run_tournament(
        "tournament.yaml",
        run_dir="runs/example",
    )


asyncio.run(main())
```

## ドキュメント

- [インストール](https://nyoki-mtl.github.io/ShogiArena/getting-started/installation.html)
- [クイックスタート](https://nyoki-mtl.github.io/ShogiArena/getting-started/quick-start.html)
- [トーナメント](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments.html)
- [SPSA](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa.html)
- [ダッシュボード](https://nyoki-mtl.github.io/ShogiArena/user-guide/dashboard.html)
- [Python API](https://nyoki-mtl.github.io/ShogiArena/api/)

## ライセンス

MIT ライセンスです。詳細は [LICENSE](LICENSE) を参照してください。
