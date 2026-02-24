# CLI Reference

`shogiarena` コマンドラインインターフェースのリファレンスです。

## 基本構文

```bash
shogiarena [グローバルオプション] <コマンド> [サブコマンド] [オプション]
```

## グローバルオプション

| オプション | 説明 |
|-----------|------|
| `--version` | バージョンを表示 |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | ログレベル（デフォルト: INFO） |
| `--debug-logger LOGGER` | 指定したロガーを DEBUG に設定（複数指定可） |
| `--output-dir PATH` | 出力ディレクトリを上書き |

## コマンド一覧

### init

設定ファイルとディレクトリを初期化します（`config init` のエイリアス）。

```bash
shogiarena init [--non-interactive] [--output-dir PATH] [--engine-dir PATH]
```

| オプション | 説明 |
|-----------|------|
| `--non-interactive`, `-y` | 対話モードを無効化（CI/自動化向け） |
| `--output-dir PATH` | 出力ディレクトリ |
| `--engine-dir PATH` | エンジンキャッシュディレクトリ |
| `--settings PATH` | settings.yaml のパス |
| `--force` | 既存設定を上書き |
| `--github-token TOKEN` | GitHub トークン（プライベートリポジトリ用） |

### config

設定の管理を行います。

```bash
shogiarena config {init,show,repo} [オプション]
```

| サブコマンド | 説明 |
|-------------|------|
| `init` | settings.yaml とディレクトリを初期化 |
| `show` | 現在の設定を表示 |
| `repo` | アーティファクトリポジトリを管理 |

### run

トーナメントや SPSA チューニングを実行します。

```bash
shogiarena run {tournament,spsa,sprt,generate,mate,analyze} [オプション]
```

#### run tournament

YAML 設定からトーナメントを実行します。

```bash
shogiarena run tournament [config.yaml] [オプション]
```

| オプション | 説明 |
|-----------|------|
| `--dry-run` | 設定を検証するのみ（実行しない） |
| `--validate-only` | 設定を検証して終了（スケジュール生成なし） |
| `--experiment-name NAME` | 実験名を上書き |
| `--run-dir PATH` | 実行ディレクトリを上書き |
| `--no-resume` | 途中再開せず新規実行 |
| `--provision {none,force}` | SSH インスタンスへのプロビジョニング |
| `--engine KEY=VALUE` | エンジン定義（複数指定可） |
| `--rules KEY=VALUE` | ルール設定の上書き |
| `--tournament KEY=VALUE` | トーナメント設定の上書き |
| `--sprt KEY=VALUE` | SPRT 設定の上書き |
| `--openbench KEY=VALUE` | OpenBench 設定の上書き |
| `--dashboard KEY=VALUE` | ダッシュボード設定の上書き |

#### run spsa

YAML 設定から SPSA チューニングを実行します。

```bash
shogiarena run spsa [config.yaml] [オプション]
```

| オプション | 説明 |
|-----------|------|
| `--dry-run` | 設定を検証するのみ |
| `--validate-only` | 設定を検証して終了 |
| `--no-resume` | 途中再開せず新規実行 |
| `--engine-trace` | USI エンジンの詳細ログを有効化 |
| `--spsa KEY=VALUE` | SPSA 設定の上書き |

#### run sprt

SPRT（Sequential Probability Ratio Test）を実行します。

```bash
shogiarena run sprt [config.yaml] [オプション]
```

| オプション | 説明 |
|-----------|------|
| `--games N` | 最大対局数（デフォルト: 400） |
| `--dry-run` | 設定を検証するのみ |
| `--validate-only` | 設定を検証して終了 |
| `--openbench KEY=VALUE` | OpenBench 設定の上書き |

`run tournament` / `run sprt` は dotted override も利用できます（例: `--openbench.target_test_id=1234`）。

#### run generate

自己対局で棋譜を生成します（`experiment_name=generate` が既定、エンジンは 1 台のみ）。設定は `generate` セクションで指定します。Generate は再開に対応しないため、毎回新規実行になります。

```bash
shogiarena run generate [config.yaml] [オプション]
```

| オプション | 説明 |
|-----------|------|
| `--dry-run` | 設定を検証するのみ（実行しない） |
| `--validate-only` | 設定を検証して終了（スケジュール生成なし） |
| `--experiment-name NAME` | 実験名を上書き |
| `--run-dir PATH` | 実行ディレクトリを上書き |
| `--no-resume` | 途中再開せず新規実行 |
| `--provision {none,force}` | SSH インスタンスへのプロビジョニング |
| `--engine KEY=VALUE` | エンジン定義（1 回のみ指定） |
| `--rules KEY=VALUE` | ルール設定の上書き |
| `--generate KEY=VALUE` | Generate 設定の上書き |
| `--dashboard KEY=VALUE` | ダッシュボード設定の上書き |
| `--system KEY=VALUE` | システム設定の上書き |

#### run mate

詰将棋を解かせます。

```bash
shogiarena run mate --engine PATH [--sfen SFEN] [--ply-limit N]
```

#### run analyze

局面を解析します。

```bash
shogiarena run analyze --engine PATH [--sfen SFEN] [--time-ms N]
```

### dashboard

保存された実行結果のダッシュボードを起動します。

```bash
shogiarena dashboard serve [--run-dir PATH] [--config PATH] [--port PORT]
```

| オプション | 説明 |
|-----------|------|
| `--run-dir PATH` | 実行ディレクトリ（`game.db` と `data/*.js` を含むディレクトリ） |
| `--config PATH` | 設定 YAML（`--run-dir` 省略時にディレクトリを推定） |
| `--port PORT` | HTTP ポート（デフォルト: 8080） |

`--run-dir` と `--config` のどちらか一方は必須です。`--config` を指定した場合、設定ファイルから実行ディレクトリを自動的に推定します。

## 使用例

### 初期設定

```bash
# 対話モードで初期化
shogiarena init

# 非対話モードで初期化
shogiarena init -y --output-dir ~/shogiarena
```

### トーナメント実行

```bash
# 設定ファイルからトーナメントを実行
shogiarena run tournament configs/run/arena/tournament.yaml

# ルールを上書きして実行
shogiarena run tournament config.yaml --rules time_control.byoyomi_ms=1000

# ドライランで設定を検証
shogiarena run tournament config.yaml --dry-run
```

### SPRT 実行

```bash
# SPRT テストを実行
shogiarena run sprt config.yaml --games 1000
```

### SPSA チューニング

```bash
# SPSA チューニングを実行
shogiarena run spsa configs/run/spsa/tune.yaml
```

### ダッシュボード起動

```bash
# 実行ディレクトリを指定して起動
shogiarena dashboard serve --run-dir output/runs/tournament/20260214_120000

# 設定ファイルから自動推定して起動
shogiarena dashboard serve --config configs/run/arena/tournament.yaml --port 9090
```

## 関連ドキュメント

- [設定ファイル](../user-guide/configuration.md) - YAML 設定の詳細
- [トーナメント](../user-guide/tournaments.md) - トーナメント実行ガイド
- [SPSA チューニング](../user-guide/spsa.md) - SPSA の使い方
