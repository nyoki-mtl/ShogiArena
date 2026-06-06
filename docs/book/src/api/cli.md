# CLI Reference

`shogiarena` コマンドラインインターフェースのリファレンスです。

## 基本構文

```bash
shogiarena [グローバルオプション] <コマンド> [サブコマンド] [オプション]
```

## グローバルオプション

| オプション                               | 説明                                        |
| ---------------------------------------- | ------------------------------------------- |
| `--version`                              | バージョンを表示                            |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | ログレベル（デフォルト: INFO）              |
| `--debug-logger LOGGER`                  | 指定したロガーを DEBUG に設定（複数指定可） |
| `--output-dir PATH`                      | 出力ディレクトリを上書き                    |

## コマンド一覧

### config

設定の管理を行います。

```bash
shogiarena config {init,show,repo} [オプション]
```

| サブコマンド | 説明                                 |
| ------------ | ------------------------------------ |
| `init`       | settings.yaml とディレクトリを初期化 |
| `show`       | 現在の設定を表示                     |
| `repo`       | アーティファクトリポジトリを管理     |

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

| オプション                                  | 説明                                                  |
| ------------------------------------------- | ----------------------------------------------------- |
| `--dry-run`                                 | 設定を検証するのみ（実行しない）                      |
| `--validate-only`                           | 設定を検証して終了（スケジュール生成なし）            |
| `--experiment-name NAME`                    | 実験名を上書き                                        |
| `--run-dir PATH`                            | 実行ディレクトリを上書き                              |
| `--no-resume`                               | 途中再開せず新規実行                                  |
| `--provision {none,force}`                  | SSH インスタンスへのプロビジョニング                  |
| `--git-worktree {strict,clean,allow-dirty}` | ビルド前の Git ワークツリーの状態チェック             |
| `--path-preflight {off,warn,error}`         | path-like USI オプションの存在確認（デフォルト: off） |
| `--engine KEY=VALUE`                        | エンジン定義（複数指定可）                            |
| `--rules KEY=VALUE`                         | ルール設定の上書き                                    |
| `--tournament KEY=VALUE`                    | トーナメント設定の上書き                              |
| `--sprt KEY=VALUE`                          | SPRT 設定の上書き                                     |
| `--openbench KEY=VALUE`                     | OpenBench 設定の上書き                                |
| `--dashboard KEY=VALUE`                     | ダッシュボード設定の上書き                            |
| `--system KEY=VALUE`                        | システム設定の上書き                                  |

#### run spsa

YAML 設定から SPSA チューニングを実行します。

```bash
shogiarena run spsa [config.yaml] [オプション]
```

| オプション                                  | 説明                                      |
| ------------------------------------------- | ----------------------------------------- |
| `--dry-run`                                 | 設定を検証するのみ                        |
| `--validate-only`                           | 設定を検証して終了                        |
| `--no-resume`                               | 途中再開せず新規実行                      |
| `--experiment-name NAME`                    | 実験名を上書き                            |
| `--run-dir PATH`                            | 実行ディレクトリを上書き                  |
| `--provision {none,force}`                  | SSH インスタンスへのプロビジョニング      |
| `--git-worktree {strict,clean,allow-dirty}` | ビルド前の Git ワークツリーの状態チェック |
| `--engine-trace`                            | USI エンジンの詳細ログを有効化            |
| `--engine KEY=VALUE`                        | エンジン定義（複数指定可）                |
| `--rules KEY=VALUE`                         | ルール設定の上書き                        |
| `--dashboard KEY=VALUE`                     | ダッシュボード設定の上書き                |
| `--spsa KEY=VALUE`                          | SPSA 設定の上書き                         |

#### run sprt

SPRT（Sequential Probability Ratio Test）を実行します。

```bash
shogiarena run sprt [config.yaml] [オプション]
```

| オプション                                  | 説明                                                  |
| ------------------------------------------- | ----------------------------------------------------- |
| `--games N`                                 | 最大対局数（デフォルト: 400、YAML なしの場合に使用）  |
| `--dry-run`                                 | 設定を検証するのみ                                    |
| `--validate-only`                           | 設定を検証して終了                                    |
| `--experiment-name NAME`                    | 実験名を上書き                                        |
| `--run-dir PATH`                            | 実行ディレクトリを上書き                              |
| `--no-resume`                               | 途中再開せず新規実行                                  |
| `--provision {none,force}`                  | SSH インスタンスへのプロビジョニング                  |
| `--git-worktree {strict,clean,allow-dirty}` | ビルド前の Git ワークツリーの状態チェック             |
| `--path-preflight {off,warn,error}`         | path-like USI オプションの存在確認（デフォルト: off） |
| `--engine KEY=VALUE`                        | エンジン定義（複数指定可）                            |
| `--rules KEY=VALUE`                         | ルール設定の上書き                                    |
| `--tournament KEY=VALUE`                    | トーナメント設定の上書き                              |
| `--sprt KEY=VALUE`                          | SPRT 設定の上書き                                     |
| `--openbench KEY=VALUE`                     | OpenBench 設定の上書き                                |
| `--dashboard KEY=VALUE`                     | ダッシュボード設定の上書き                            |
| `--system KEY=VALUE`                        | システム設定の上書き                                  |

`run tournament` / `run sprt` は dotted override も利用できます（例: `--openbench.target_test_id=1234`）。

#### run generate

自己対局で棋譜を生成します（`experiment_name=generate` が既定、エンジンは 1 台のみ）。設定は `generate` セクションで指定します。Generate は再開に対応しないため、毎回新規実行になります。

```bash
shogiarena run generate [config.yaml] [オプション]
```

| オプション                          | 説明                                                  |
| ----------------------------------- | ----------------------------------------------------- |
| `--dry-run`                         | 設定を検証するのみ（実行しない）                      |
| `--validate-only`                   | 設定を検証して終了（スケジュール生成なし）            |
| `--experiment-name NAME`            | 実験名を上書き                                        |
| `--run-dir PATH`                    | 実行ディレクトリを上書き                              |
| `--no-resume`                       | 途中再開せず新規実行                                  |
| `--provision {none,force}`          | SSH インスタンスへのプロビジョニング                  |
| `--path-preflight {off,warn,error}` | path-like USI オプションの存在確認（デフォルト: off） |
| `--engine KEY=VALUE`                | エンジン定義（1 回のみ指定）                          |
| `--rules KEY=VALUE`                 | ルール設定の上書き                                    |
| `--generate KEY=VALUE`              | Generate 設定の上書き                                 |
| `--dashboard KEY=VALUE`             | ダッシュボード設定の上書き                            |
| `--system KEY=VALUE`                | システム設定の上書き                                  |

#### run mate

詰将棋を解かせます。

```bash
shogiarena run mate <engine> [position] [オプション]
```

- `engine`: エンジンバイナリまたは YAML 設定ファイルのパス（位置引数）
- `position`: USI 形式の局面文字列（位置引数、デフォルト: `startpos`）

| オプション           | 説明                                     |
| -------------------- | ---------------------------------------- |
| `--ply-limit N`      | 最大探索手数                             |
| `--node-limit N`     | 最大探索ノード数                         |
| `--infinite`         | `go mate infinite` を送信                |
| `--wait-bestmove`    | `checkmate` 後に `bestmove` を待つ       |
| `--timeout SEC`      | タイムアウト時間（秒）                   |
| `--option KEY=VALUE` | エンジンオプションの上書き（複数指定可） |

`--ply-limit` と `--node-limit` は同時指定できません。`--infinite` は両者と排他です。

#### run analyze

局面を解析します。

```bash
shogiarena run analyze <engine> [position] [オプション]
```

- `engine`: エンジンバイナリまたは YAML 設定ファイルのパス（位置引数）
- `position`: USI 形式の局面文字列（位置引数、デフォルト: `startpos`）

| オプション            | 説明                                                   |
| --------------------- | ------------------------------------------------------ |
| `--movetime MS`       | 固定思考時間（ミリ秒）                                 |
| `--nodes N`           | 探索ノード数制限                                       |
| `--depth N`           | 探索深さ制限                                           |
| `--infinite`          | 無限探索（他の制限が指定されていない場合のデフォルト） |
| `--ponder`            | ponder フラグを設定                                    |
| `--searchmoves M ...` | 探索する手を制限（USI 形式）                           |
| `--timeout SEC`       | タイムアウト時間（秒）                                 |
| `--quiet`             | 途中の info 行を非表示                                 |
| `--option KEY=VALUE`  | エンジンオプションの上書き（複数指定可）               |

### dashboard

保存された実行結果のダッシュボードを起動します。

```bash
shogiarena dashboard serve [--run-dir PATH] [--config PATH] [--port PORT]
```

| オプション       | 説明                                                            |
| ---------------- | --------------------------------------------------------------- |
| `--run-dir PATH` | 実行ディレクトリ（`game.db` と `data/*.js` を含むディレクトリ） |
| `--config PATH`  | 設定 YAML（`--run-dir` 省略時にディレクトリを推定）             |
| `--port PORT`    | HTTP ポート（デフォルト: 8080）                                 |

`--run-dir` と `--config` のどちらか一方は必須です。`--config` を指定した場合、設定ファイルから実行ディレクトリを自動的に推定します。

### results

保存された `game.db` または run ディレクトリから結果を集計します。

```bash
shogiarena results summary <run-dir-or-game.db> [オプション]
```

| オプション                 | 説明                                   |
| -------------------------- | -------------------------------------- |
| `--format {text,json,csv}` | 出力形式（デフォルト: text）           |
| `--confidence FLOAT`       | スコア率の信頼区間（デフォルト: 0.95） |
| `--engine NAME`            | 表示するエンジン名を限定（複数指定可） |

#### summary JSON schema v1

`--format json` のトップレベルは `schema_version: 1` を持ちます。主要フィールドは以下です。

| フィールド              | 説明                                                                         |
| ----------------------- | ---------------------------------------------------------------------------- |
| `source`                | 入力パス                                                                     |
| `run_dir`               | run ディレクトリ。`game.db` 直指定時は親ディレクトリ                         |
| `shogiarena_version`    | run artifact から読める ShogiArena バージョン                                |
| `total_scheduled_games` | 予定対局数。manifest または `tournament_results.json` 由来                   |
| `completed_games`       | `game.db` に保存された完了対局数                                             |
| `incomplete_games`      | `total_scheduled_games - completed_games`                                    |
| `failed_games`          | `tournament_results.json` の cancelled count、なければ failure artifact 件数 |
| `engines`               | エンジン別 W/D/L、score rate、side split、信頼区間                           |
| `raw_result_counts`     | DB に保存された raw `game_result` の件数                                     |
| `failures`              | `run_failures.json` / `run_failures.jsonl` から読んだ失敗レコード            |
| `failures_by_phase`     | `failure_phase` 別の失敗件数                                                 |

失敗レコード artifact も `schema_version: 1` を持ち、`failures` 配列を含みます。各要素は `game_id`, `scheduled_black_engine`, `scheduled_white_engine`, `failure_phase`, `exception_class`, `short_message`, `occurred_at`, `engine`, `log_artifact_path` を持ち、起動失敗では `diagnostic` に resolved executable、working directory、command、options が入ります。

#### results verify-provenance

run マニフェスト（`manifest.json`）の provenance ハッシュを、ディスク上の実ファイルと照合します。

```bash
shogiarena results verify-provenance <run-dir>
```

`<run-dir>` は `manifest.json` を含む run ディレクトリです。マニフェストが provenance シール済み
（`status: provenance_sealed`）であることを前提とし、未シールの場合は検証失敗となります。結果は
`schema_version: 1` の JSON で出力され、`ok`（真偽）と `failures`（不一致パスとその理由の配列）を含みます。
検証に失敗した場合は非ゼロ終了します。

## 使用例

### 初期設定

```bash
# 対話モードで初期化
shogiarena config init

# 非対話モードで初期化
shogiarena config init -y --output-dir ~/shogiarena/output --engine-dir ~/shogiarena/engines
```

### トーナメント実行

```bash
# 設定ファイルからトーナメントを実行
shogiarena run tournament configs/run/tournament/example.yaml

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
shogiarena dashboard serve --run-dir output/runs/my-run-1a2b3c4d/20260214120000

# 設定ファイルから自動推定して起動
shogiarena dashboard serve --config configs/run/tournament/example.yaml --port 9090
```

## 関連ドキュメント

- [設定ファイル](../user-guide/configuration.md) - YAML 設定の詳細
- [トーナメント](../user-guide/tournaments.md) - トーナメント実行ガイド
- [SPSA チューニング](../user-guide/spsa.md) - SPSA の使い方
