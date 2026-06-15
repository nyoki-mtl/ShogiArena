# CLI

ShogiArena の CLI は `shogiarena` コマンドです。

```bash
shogiarena [global-options] <command> ...
```

## グローバルオプション

| オプション | 説明 |
| --- | --- |
| `--version` | バージョンを表示する |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | ログレベル |
| `--debug-logger LOGGER` | 指定 logger を DEBUG にする |
| `--output-dir PATH` | この実行だけ標準出力先を上書きする |

## コマンド一覧

| コマンド | 用途 |
| --- | --- |
| `config` | `settings.yaml` と artifact リポジトリ設定 |
| `run` | tournament / sprt / spsa / generate / mate / analyze |
| `results` | run 結果の集計と provenance 検証 |
| `dashboard` | 保存済み run のダッシュボード表示 |
| `replay-position` | 保存済み局面の再検索 |

## `config`

```bash
shogiarena config init
shogiarena config show
shogiarena config repo set yaneuraou --path ~/repos/YaneuraOu
```

`config init` は標準の `output_dir`、`engine_dir`、artifact 用の設定を作ります。

## `run`

```bash
shogiarena run tournament tournament.yaml
shogiarena run sprt sprt.yaml
shogiarena run spsa spsa.yaml
shogiarena run generate generate.yaml
```

### `run tournament`

```bash
shogiarena run tournament [config.yaml] [options]
```

主なオプション:

| オプション | 説明 |
| --- | --- |
| `--dry-run` | 設定を検証して実行しない |
| `--validate-only` | 設定検証のみ |
| `--experiment-name NAME` | 自動 run 名を上書き |
| `--run-dir PATH` | run ディレクトリを明示指定 |
| `--no-resume` | 再開せず新規実行 |
| `--provision {none,force}` | SSH インスタンスへの配置制御 |
| `--path-preflight {off,warn,error}` | パス系 USI オプションの事前検査 |

YAML の一部は CLI から上書きできます。

```bash
shogiarena run tournament tournament.yaml \
  --tournament games_per_pair=100 num_parallel=4 \
  --rules time_control.byoyomi_ms=1000
```

### `run sprt`

SPRT 用の設定を実行します。

```bash
cp examples/configs/run/sprt/example.yaml sprt.yaml
shogiarena run sprt sprt.yaml
```

### `run spsa`

SPSA チューニングを実行します。

```bash
cp examples/configs/run/spsa/example.yaml spsa.yaml
shogiarena run spsa spsa.yaml
```

### `run generate`

自己対局で棋譜を生成します。

```bash
cp examples/configs/run/generate/example.yaml generate.yaml
shogiarena run generate generate.yaml
```

### `run mate` / `run analyze`

単一エンジンで局面を探索します。

```bash
shogiarena run mate engine.yaml startpos --ply-limit 5
shogiarena run analyze engine.yaml startpos --nodes 100000
```

## `dashboard`

```bash
shogiarena dashboard serve --run-dir /path/to/run
shogiarena dashboard serve --config tournament.yaml
shogiarena dashboard serve --run-dir /path/to/run --port 9090
```

## `results`

```bash
shogiarena results summary /path/to/run
shogiarena results summary /path/to/run --format json
shogiarena results summary /path/to/game.db --engine EngineA
shogiarena results verify-provenance /path/to/run
```

## `replay-position`

保存済みの対局局面を、指定エンジンで再探索します。

```bash
shogiarena replay-position --run-dir /path/to/run \
  --game-id g0001-abc \
  --ply 80 \
  --engine EngineA \
  --nodes 100000
```

USI transcript を保存した run では、履歴を再現した再検索にも使えます。
