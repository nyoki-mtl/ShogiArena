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

> **Note**: `shogiarena init` は `shogiarena config init` の互換エイリアスです。正準コマンドは `config init` です。

| オプション | 説明 |
| --- | --- |
| `--non-interactive` | 非対話モード: コマンドライン引数を使用（CI/自動化用）。 |
| `--output-dir PATH` | 出力ディレクトリ（デフォルト: OS 標準の出力領域）。 |
| `--engine-dir PATH` | エンジンキャッシュディレクトリ（デフォルト: OS 標準のキャッシュ領域）。 |
| `--settings PATH` | 設定ファイルの出力先（デフォルト: プラットフォーム標準の config dir）。 |
| `--github-token TOKEN` | GitHub トークン（private repo 用）。 |
| `--force` | 既存の設定やディレクトリを上書きします。 |

## run mate - 詰将棋探索

エンジンの `go mate` コマンドを使用して詰将棋を解かせます。

```bash
shogiarena run mate <engine> [position] [options]
```

### オプション

| オプション | 説明 |
| --- | --- |
| `--ply-limit PLY` | 最大探索手数（深さ）。 |
| `--timeout SEC` | タイムアウト時間。 |

## run generate - 棋譜生成（selfplay）

自己対局で棋譜や局面データを生成します。YAML で設定します。

```bash
shogiarena run generate <config.yaml>
```

## config - パス設定管理

`settings.yaml` で管理されている出力先、エンジンキャッシュ、repo 定義などを確認・変更します。
private repo を使う場合は `github_token` を設定します（Contents: Read-only）。

```bash
shogiarena config show
shogiarena config repo set <name> --path <path> --url <url> --build-config <path>
shogiarena config repo remove <name>
```

