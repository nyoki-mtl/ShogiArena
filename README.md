# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-docs.yml/badge.svg)](https://nyoki-mtl.github.io/ShogiArena/index.html)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

ShogiArenaは、USI対応の将棋エンジンを自動で対局させ、結果を比較するPython製のツールです。
エンジンの強さの検定、パラメータの調整、自己対局による学習用棋譜の生成にも使えます。

[ドキュメント](https://nyoki-mtl.github.io/ShogiArena/) |
[クイックスタート](https://nyoki-mtl.github.io/ShogiArena/getting-started/quick-start.html) |
[PyPI](https://pypi.org/project/shogiarena/) |
[変更履歴](CHANGELOG.md)

## できること

| やりたいこと | ShogiArenaの機能 |
|---|---|
| エンジン同士を対局させる | 総当たり、基準エンジンとの対局（gauntlet）、並列実行 |
| 変更前後の強さを比べる | 対局結果から統計的に検定するSPRT（GSPRT） |
| パラメータを調整する | 対局結果を使ってUSIオプションを調整するSPSA |
| 学習用の棋譜を作る | 自己対局、SFEN／KIF／CSA／バイナリ形式での出力 |
| 複数のマシンで対局を回す | SSH接続によるリモート実行 |

設定、棋譜、対局結果は実行ごとに保存されます。
途中で止めたトーナメントの再開や、保存した結果の集計もできます。

## ダッシュボード

https://github.com/user-attachments/assets/1cdebe23-b1a9-4d8e-91c0-f56ca970b569

ブラウザで進行中の対局や順位表を確認し、棋譜を再生できます。
SPRTの判定やSPSAの更新状況も表示でき、終了後に保存した結果を開き直すこともできます。

## インストール

Python 3.11以上が必要です。
Windows x86_64、Linux x86_64／arm64、macOS Intel／Apple Siliconで動作します。

```bash
pip install shogiarena
shogiarena --version
```

対局に使うUSIエンジンの実行ファイルと、そのエンジンに必要な評価関数は別途用意してください。
エンジンの入手から試したい場合は、[クイックスタート](https://nyoki-mtl.github.io/ShogiArena/getting-started/quick-start.html)にYaneuraOuと水匠5を取得する手順があります。

インストールの詳細は[インストールガイド](https://nyoki-mtl.github.io/ShogiArena/getting-started/installation.html)を参照してください。
SSHでのリモート実行先はLinux x86_64のみ対応しています。

## 最初のトーナメント

手元のUSIエンジンを二つ登録して、動作確認用に10局対局させます。
同じエンジンの設定違いを比較することもできます。

まず、次の三つのファイルを同じディレクトリに作ります。
`engine_path`は実行ファイルのパスに置き換えてください。
`working_directory`には、エンジンの作業ディレクトリ（通常は実行ファイルのあるディレクトリ）を指定します。
Windowsでは`C:/engines/engine_a.exe`のように、`/`で区切る書き方も使えます。

`engine_a.yaml`:

```yaml
name: "Engine A"
engine_path: "/path/to/engine_a"
working_directory: "/path/to"
options:
  Threads: 2
  USI_Hash: 256
```

`engine_b.yaml`:

```yaml
name: "Engine B"
engine_path: "/path/to/engine_b"
working_directory: "/path/to"
options:
  Threads: 2
  USI_Hash: 256
```

`Threads`と`USI_Hash`は、それぞれスレッド数とハッシュメモリ量（MB）の指定例です。
使えるオプションはエンジンによって異なるので、必要な評価関数のパスなども含めて、[エンジン設定](https://nyoki-mtl.github.io/ShogiArena/user-guide/engine-configuration.html)を参考に調整してください。

`tournament.yaml`:

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

この設定では2局を並列に進めます。
各エンジンの持ち時間は10秒で、1手ごとに0.1秒加算します。

ファイルを作ったディレクトリで、設定を確認してから実行します。
`--dry-run`では設定と対局予定を確認できますが、エンジンは起動しません。

```bash
shogiarena run tournament tournament.yaml --dry-run --run-dir runs/first_tournament
shogiarena run tournament tournament.yaml --run-dir runs/first_tournament
```

実行中は[http://localhost:8080](http://localhost:8080)でダッシュボードを開けます。
棋譜や対局結果は`runs/first_tournament`に保存されます。

## 結果を見る

終了後も、保存先を指定すればダッシュボードで結果や棋譜を確認できます。

```bash
shogiarena dashboard serve --run-dir runs/first_tournament
```

端末で勝敗を集計したり、JSONやCSVで出力したりすることもできます。

```bash
shogiarena results summary runs/first_tournament
shogiarena results summary runs/first_tournament --format json
shogiarena results summary runs/first_tournament --format csv
```

対局結果を強さの比較に使うときは、`completion_status.json`で終了状態も確認してください。
正常完了と途中停止の違いや、集計から除外される対局の扱いは、[トーナメントガイド](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments.html)で説明しています。

## Pythonから使う

同じ設定ファイルを使って、Pythonスクリプトからトーナメントを実行できます。

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

USIエンジンを直接操作して局面を解析するAPIもあります。
使い方は[Pythonライブラリガイド](https://nyoki-mtl.github.io/ShogiArena/user-guide/python-library.html)、関数や型の一覧は[APIリファレンス](https://nyoki-mtl.github.io/ShogiArena/api/index.html)を参照してください。

## 詳しく使う

用途別の設定テンプレートは[examples/configs](examples/configs)にあります。
`pip install`でインストールした環境では、GitHubから取得してください。

- [インストール](https://nyoki-mtl.github.io/ShogiArena/getting-started/installation.html)
- [最初のトーナメント](https://nyoki-mtl.github.io/ShogiArena/getting-started/first-tournament.html)
- [トーナメント](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments.html)
- [SPRT／SPSA](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa.html)
- [リモート実行](https://nyoki-mtl.github.io/ShogiArena/user-guide/remote-execution.html)
- [エンジン設定](https://nyoki-mtl.github.io/ShogiArena/user-guide/engine-configuration.html)
- [ダッシュボード](https://nyoki-mtl.github.io/ShogiArena/user-guide/dashboard.html)
- [Pythonライブラリ](https://nyoki-mtl.github.io/ShogiArena/user-guide/python-library.html)
- [CLIリファレンス](https://nyoki-mtl.github.io/ShogiArena/api/cli.html)

開発環境の準備や変更の検証方法は、[コントリビュートガイド](https://nyoki-mtl.github.io/ShogiArena/development/contributing.html)にまとめています。

## ライセンス

ShogiArenaは[MIT License](LICENSE)で公開しています。
