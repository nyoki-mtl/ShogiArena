# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-docs.yml/badge.svg)](https://nyoki-mtl.github.io/ShogiArena/index.html)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

ShogiArena は、USI 将棋エンジン同士の自動対局、トーナメント管理、SPRT による統計検定、SPSA パラメータチューニングをまとめて扱う実行基盤です。
設定ファイルを書いて CLI から実行することも、Python API から呼び出して自動化することもできます。

> [!NOTE]
> v1.0.0以降の安定APIは、CLI、公開設定schema、および`shogiarena.engine` / `shogiarena.tournament`の通常利用向け入口です。
> `shogiarena.composition`と高度なrunner/storage構築用型はprovisionalであり、1.xでも変更される場合があります。詳細は[公開API](https://nyoki-mtl.github.io/ShogiArena/api/index.html)を参照してください。

**ドキュメント:** [https://nyoki-mtl.github.io/ShogiArena/](https://nyoki-mtl.github.io/ShogiArena/)

## デモ

https://github.com/user-attachments/assets/1cdebe23-b1a9-4d8e-91c0-f56ca970b569

リアルタイム更新に対応したダッシュボードで、進行中の対局、順位表、棋譜、SPSA の更新状況を確認できます。

## できること

- `round_robin` / `gauntlet` 形式のトーナメント実行
- 2 エンジン間の GSPRT / SPRT 検定と早期停止
- SPSA による USI オプションと評価パラメータのチューニング
- 実行中と完了後の Web ダッシュボード表示
- SFEN / KIF / CSA などの棋譜保存と結果集計
- エンジン内蔵定跡の検証、provenance 記録、Book タブでの out-of-book 分析
- ローカル実行と SSH インスタンスを使った分散実行
- `shogiarena.engine` / `shogiarena.tournament` による Python からの自動化

## インストール

Python 3.11 以上が必要です。

```bash
pip install shogiarena
```

ビルド済み wheel は Windows x86_64、Linux x86_64 / arm64、macOS Intel / Apple Silicon に提供されます（Windows on ARM は対象外）。
x86_64 で AVX2 版へ差し替える方法は[インストール](https://nyoki-mtl.github.io/ShogiArena/getting-started/installation.html)を参照してください。

ソースから開発する場合は `uv` を使います。

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync
uv run shogiarena --help
```

出力先やエンジン配置先の既定値は、次のコマンドで初期化できます。

```bash
shogiarena config init
```

初期化しておくと、`{output_dir}` / `{engine_dir}` プレースホルダー、artifact ベースのエンジン解決、共有キャッシュを設定ファイルから利用できます。

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

`--dry-run` で設定を検証し、問題がなければそのまま実行します。

```bash
shogiarena run tournament tournament.yaml --dry-run
shogiarena run tournament tournament.yaml
```

`dashboard.enabled: true` の場合は `http://localhost:8080` でダッシュボードを開けます。

## よく使うコマンド

以下の `examples/` はこのリポジトリのテンプレートです。
pip でインストールした場合は同梱されないため、[examples ディレクトリ](https://github.com/nyoki-mtl/ShogiArena/tree/main/examples/configs)から取得してください。

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

公開入口は次のモジュールです。

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

`shogiarena._core` 配下は内部実装です。import できても後方互換性は保証されません。
CLI、公開設定schema、`create_engine*()`、`UsiEngineSession`、`load_tournament_config()`、`run_tournament()`、`TournamentRunResult`は安定面です。
`shogiarena.composition`と`build_tournament_runner()`等の高度な組み立てAPIはprovisional面です。

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
