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
shogiarena config repo set yaneuraou \
  --path ~/repos/YaneuraOu \
  --url https://github.com/yaneurao/YaneuraOu.git \
  --build-config ~/.config/shogiarena/builds/yaneuraou.yaml
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
| `--git-worktree {strict,clean,allow-dirty}` | artifact build 前の Git worktree 扱い |
| `--path-preflight {off,warn,error}` | パス系 USI オプションの事前検査 |
| `--engine KEY=VALUE ...` | engine 定義を CLI から追加（repeatable） |
| `--rules KEY=VALUE ...` | `rules.*` を上書き |
| `--tournament KEY=VALUE ...` | `tournament.*` を上書き |
| `--rating KEY=VALUE ...` | `rating.*` を上書き |
| `--dashboard KEY=VALUE ...` | `dashboard.*` を上書き |
| `--logging KEY=VALUE ...` | `logging.*` を上書き |
| `--system KEY=VALUE ...` | `system.*` を上書き |
| `--sprt KEY=VALUE ...` | `sprt.*` を上書き |
| `--openbench KEY=VALUE ...` | `openbench.*` を上書き |

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

`run analyze` の主なオプション:

| オプション | 説明 |
| --- | --- |
| `--nodes N` | 探索ノード数 |
| `--depth N` | 探索深さ |
| `--movetime MS` | 固定思考時間 |
| `--infinite` | `go infinite` を使う |
| `--ponder` | `go` に ponder flag を付ける |
| `--searchmoves MOVE ...` | 探索対象手を USI move で制限する |
| `--timeout SEC` | `bestmove` 待機の timeout |
| `--quiet` | 中間 info line を抑制する |
| `--option KEY=VALUE` | engine option を上書き（repeatable） |

`run mate` の主なオプション:

| オプション | 説明 |
| --- | --- |
| `--ply-limit N` | 詰み探索の ply 上限 |
| `--node-limit N` | 詰み探索の node 上限 |
| `--infinite` | `go mate infinite` を使う |
| `--wait-bestmove` | mate result 後の trailing `bestmove` を待つ |
| `--timeout SEC` | mate result 待機の timeout |
| `--option KEY=VALUE` | engine option を上書き（repeatable） |

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

`summary --format json` には timing metadata が含まれます。`engine_wall_time_ms` は engine の `think()` 呼び出しから `bestmove` 回収までの engine I/O 窓、`wall_time_ms` は持ち時間に課金された wall time です。wall NPS の既定 field は `engine_wall_time_ms` です。

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
