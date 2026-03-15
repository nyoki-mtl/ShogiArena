# Utility Tools

Shogi Arena はトーナメント実行以外にも、エンジンの検証や環境構築に役立つ CLI ツールを提供しています。

## config init - 環境初期化

Shogi Arena の実行に必要なディレクトリ構造と設定ファイル (`settings.yaml`) を初期化します。

**デフォルトでは対話的に実行されます**。対話的に設定を入力することで、YaneuraOuリポジトリの追加やビルド設定の自動生成などが行われます。

```bash
# 対話的モード（デフォルト）
shogiarena config init

# 非対話的モード（CI/自動化用）
shogiarena config init --non-interactive --output-dir /path/to/output --engine-dir /path/to/engines
```

> **Note**: 初期化コマンドは `shogiarena config init` です。

| オプション | 説明 |
| --- | --- |
| `--non-interactive` | 非対話モード: コマンドライン引数を使用（CI/自動化用）。 |
| `--output-dir PATH` | 出力ディレクトリ（デフォルト: OS 標準の出力領域）。 |
| `--engine-dir PATH` | エンジンキャッシュディレクトリ（デフォルト: OS 標準のキャッシュ領域）。 |
| `--settings PATH` | 設定ファイルの出力先（デフォルト: プラットフォーム標準の config dir）。 |
| `--github-token TOKEN` | GitHub トークン（private repo 用）。 |
| `--force` | 既存の設定やディレクトリを上書きします。 |

`config init` の対話モードでは OpenBench/ShogiBench 接続情報（`server`, `username`, `password_env`）も設定できます。
パスワード本体は保存せず、`password_env` で指定した環境変数から読み込みます。

## run mate - 詰将棋探索

エンジンの `go mate` コマンドを使用して詰将棋を解かせます。

```bash
shogiarena run mate <engine> [position] [options]
```

### オプション

| オプション | 説明 |
| --- | --- |
| `--ply-limit PLY` | 最大探索手数（深さ）。 |
| `--node-limit NODES` | 最大探索ノード数。 |
| `--infinite` | `go mate infinite` を送信。 |
| `--wait-bestmove` | `checkmate` 後に trailing `bestmove` 受信まで待機。 |
| `--timeout SEC` | タイムアウト時間。 |
| `--option KEY=VALUE` | エンジンオプションの上書き（複数指定可）。 |

`--ply-limit` と `--node-limit` は同時指定できません。`--infinite` は両者と排他です。

## run generate - 棋譜生成（selfplay）

自己対局で棋譜や局面データを生成します。YAML で設定します（エンジンは 1 台、再開は非対応）。

```bash
shogiarena run generate <config.yaml>
```

### Generate 設定

`generate` セクションで対局数と並列数を指定します。

```yaml
generate:
  games: 100
  num_parallel: 4
  seed: 42
```

### 出力の制御

`run generate` では `records_output` セクションで `format` を `sbinpack` または `psv` に切り替えてバイナリ棋譜を落とせます。設定例：

```yaml
records_output:
  format: sbinpack
  output_dir: /path/to/output/records
  max_games_per_file: 500
```

生成条件や実行時メタデータ（実験名、レコード形式、エンジン設定など）は run_dir のメタデータに保存され、DB を使わずに参照できます。

ダッシュボードの **Generate** タブでは、生成ファイル数や総対局数/局面数/バイト数が参照でき、実行中もライブ更新されます。

## config - パス設定管理

`settings.yaml` で管理されている出力先、エンジンキャッシュ、repo 定義などを確認・変更します。
private repo を使う場合は `github_token` を設定します（Contents: Read-only）。
OpenBench を設定した場合は `config show` に `openbench` セクションも表示されます。

```bash
shogiarena config show
shogiarena config repo set <name> --path <path> --url <url> --build-config <path>
shogiarena config repo remove <name>
```
