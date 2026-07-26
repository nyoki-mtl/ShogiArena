# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-docs.yml/badge.svg)](https://nyoki-mtl.github.io/ShogiArena/index.html)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

USI将棋エンジンの対局、比較、チューニングを、一つの実行環境で管理します。

ShogiArenaは、トーナメント、GSPRT、SPSA、自己対局による棋譜生成に対応しています。
対局の実行だけでなく、設定の検証、棋譜と統計の保存、異常の診断、Webダッシュボードでの監視までを同じrunとして記録します。

[ドキュメント](https://nyoki-mtl.github.io/ShogiArena/) |
[クイックスタート](https://nyoki-mtl.github.io/ShogiArena/getting-started/quick-start.html) |
[PyPI](https://pypi.org/project/shogiarena/) |
[変更履歴](CHANGELOG.md)

## ダッシュボード

https://github.com/user-attachments/assets/1cdebe23-b1a9-4d8e-91c0-f56ca970b569

進行中の対局、順位表、棋譜、エンジン設定、SPRT、SPSAの更新状況をリアルタイムに確認できます。
完了したrunも同じ画面で開き直せます。

## 用途

| やりたいこと | ShogiArenaの機能 |
|---|---|
| 複数エンジンを比較する | round-robin、gauntlet、並列対局 |
| 変更の強さを検定する | trinomial／pentanomial GSPRT、早期停止 |
| USIオプションを調整する | SPSA、共有乱数、LTC回帰テスト |
| 学習用の棋譜を作る | 自己対局、SFEN／KIF／CSA／binary records |
| 対局を監視して調べる | Webダッシュボード、USI transcript、結果集計 |
| 複数ホストで実行する | ローカルinstance、SSH instance pool |

各runには設定、エンジン情報、棋譜、SQLite DB、再開状態が保存されます。
v1.1.0以降は`completion_status.json`も保存されるため、正常完了、SPRTの早期決着、利用者による停止、安全装置による停止を区別できます。

## インストール

Python 3.11以上と、実行するUSI将棋エンジンが必要です。

```bash
pip install shogiarena
shogiarena --version
```

Windows x86_64、Linux x86_64／arm64、macOS Intel／Apple Silicon向けのwheelを提供しています。
AVX2版の`rsshogi`へ差し替える場合は、[インストールガイド](https://nyoki-mtl.github.io/ShogiArena/getting-started/installation.html)を参照してください。

出力先とエンジン配置先を初期化する場合は、次のコマンドを実行します。

```bash
shogiarena config init
shogiarena config show
```

`config init`は必須ではありません。
`{output_dir}`と`{engine_dir}`のプレースホルダー、artifact repository、共有キャッシュを使う場合に設定してください。

## 最初のトーナメント

ローカルのUSIエンジンを二つ登録し、10局のround-robinを実行します。

まず、エンジンごとに設定ファイルを作ります。

`engine_a.yaml`:

```yaml
name: "Engine A"
engine_path: "/path/to/engine_a"
options:
  Threads: 2
  USI_Hash: 256
```

`engine_b.yaml`:

```yaml
name: "Engine B"
engine_path: "/path/to/engine_b"
options:
  Threads: 2
  USI_Hash: 256
```

次に、二つのエンジンを参照する`tournament.yaml`を作ります。

```yaml
experiment_name: "first_tournament"

engines:
  - engine_path: "engine_a.yaml"
  - engine_path: "engine_b.yaml"

tournament:
  scheduler: "round_robin"
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

実行前に`--dry-run`で設定を検証します。

```bash
shogiarena run tournament tournament.yaml --dry-run
shogiarena run tournament tournament.yaml
```

実行中は`http://localhost:8080`でダッシュボードを開けます。
設定項目の意味とrunディレクトリの構成は、[クイックスタート](https://nyoki-mtl.github.io/ShogiArena/getting-started/quick-start.html)にまとめています。

## 実行モード

| コマンド | 用途 |
|---|---|
| `shogiarena run tournament CONFIG` | round-robinまたはgauntletを実行する |
| `shogiarena run sprt CONFIG` | 二つのエンジンをGSPRTで比較する |
| `shogiarena run spsa CONFIG` | USIオプションや評価パラメータを調整する |
| `shogiarena run generate CONFIG` | 自己対局で棋譜を生成する |
| `shogiarena run analyze` | 一局面を通常探索する |
| `shogiarena run mate` | 一局面を詰み探索する |

設定テンプレートは[examples/configs](examples/configs)にあります。
PyPIの配布物には含まれないため、必要な場合はGitHubから取得してください。

## 保存したrunを調べる

ダッシュボードは実行中だけでなく、保存済みのrunにも利用できます。

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

対局結果を端末、JSON、CSVへ集計できます。

```bash
shogiarena results summary /path/to/run
shogiarena results summary /path/to/run --format json
shogiarena results summary /path/to/run --format csv
```

v1.1.0以降の測定結果を採用する前に、`completion_status.json`の`status`と`termination_reason`を確認してください。
ShogiArena自身の停滞が疑われる時間切れと、原因を断定できない時間切れは無効局として記録され、EloとSPRTの標本から除外されます。

## Pythonから使う

公開APIからUSIエンジンを起動し、局面を探索できます。

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine_a.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=5_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

保存先を指定してトーナメントを実行する場合は、`shogiarena.tournament`を使います。

```python
import asyncio

from shogiarena.tournament import run_tournament


async def main() -> None:
    result = await run_tournament(
        "tournament.yaml",
        run_dir="runs/example",
    )
    print(result)


asyncio.run(main())
```

安定した公開面は、CLI、公開設定schema、`shogiarena.engine`、`shogiarena.tournament`の通常利用向けAPIです。
`shogiarena.composition`の高度なrunner／storage組み立てAPIはprovisionalであり、1.xでも変更される場合があります。
`shogiarena._core`は内部実装なので直接importしないでください。

## ドキュメント

- [インストール](https://nyoki-mtl.github.io/ShogiArena/getting-started/installation.html)
- [最初のトーナメント](https://nyoki-mtl.github.io/ShogiArena/getting-started/first-tournament.html)
- [トーナメント](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments.html)
- [SPRT／SPSA](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa.html)
- [エンジン設定](https://nyoki-mtl.github.io/ShogiArena/user-guide/engine-configuration.html)
- [ダッシュボード](https://nyoki-mtl.github.io/ShogiArena/user-guide/dashboard.html)
- [Pythonライブラリ](https://nyoki-mtl.github.io/ShogiArena/user-guide/python-library.html)
- [CLIリファレンス](https://nyoki-mtl.github.io/ShogiArena/api/cli.html)
- [トラブルシューティング](https://nyoki-mtl.github.io/ShogiArena/troubleshooting.html)

## 開発

このリポジトリの開発環境は`uv`で管理します。

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync
make check
```

変更を送る前に[コントリビュートガイド](https://nyoki-mtl.github.io/ShogiArena/development/contributing.html)を確認してください。

## ライセンス

ShogiArenaは[MIT License](LICENSE)で公開しています。
