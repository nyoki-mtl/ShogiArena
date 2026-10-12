# CLI

ShogiArena の CLI は `shogiarena` コマンドです。

```bash
shogiarena [global-options] <command> ...
```

## グローバルオプション

| オプション | 説明 |
| --- | --- |
| `--version` | バージョンを表示する |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | ログレベルを指定する |
| `--debug-logger LOGGER` | 指定したロガーをDEBUGにする |
| `--output-dir PATH` | この実行だけ標準出力先を上書きする |

## コマンド一覧

| コマンド | 用途 |
| --- | --- |
| `config` | `settings.yaml` と artifact リポジトリ設定 |
| `run` | tournament / sprt / spsa / generate / mate / analyze |
| `results` | 対局結果の集計と使用したエンジンの検証 |
| `dashboard` | 保存済み run の表示と追記中CSAログの監視 |
| `csa` | CSAログの状態確認と棋譜の書き出し |
| `replay-position` | 保存済み局面の再探索 |
| `worker-bundle` | リモート実行用のパッケージと配置済みファイルの一覧を生成 |

## `config`

```bash
shogiarena config init
shogiarena config show
shogiarena config repo set yaneuraou \
  --path ~/repos/YaneuraOu \
  --url https://github.com/yaneurao/YaneuraOu.git \
  --build-config ~/.config/shogiarena/builds/yaneuraou.yaml
```

`config init` は標準の `output_dir`、`engine_dir`、artifact 用の設定を作成します。

## `worker-bundle`

```bash
shogiarena worker-bundle build --output /absolute/path/worker-bundle.zip
shogiarena worker-bundle preplaced-map \
  --engine ENGINE_NAME LOCAL_BINARY /absolute/remote/engine \
  --resource ENGINE_NAME LOCAL_FILE_OR_DIRECTORY /absolute/remote/resource
```

`preplaced-map`はエンジンや評価関数などのハッシュ値を計算し、`--provision preplaced`で使う
`SHOGIARENA_REMOTE_PREPLACED_RESOURCES`の単一行JSONを標準出力へ返します。
`--engine`と`--resource`は繰り返し指定できます。

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

主なオプションは次のとおりです。

| オプション | 説明 |
| --- | --- |
| `--dry-run` | 設定を検証して実行しない |
| `--validate-only` | 設定の検証だけを行う |
| `--experiment-name NAME` | 自動生成の run 名を上書きする |
| `--run-dir PATH` | run ディレクトリを明示指定する |
| `--no-resume` | 再開せず新規に実行する |
| `--provision {cas,preplaced}` | リモートへファイルを転送するか、検証済みの配置済みファイルを使うかを指定する |
| `--git-worktree {strict,clean,allow-dirty}` | エンジンのビルド前にGit作業ツリーをどう扱うか指定する |
| `--path-preflight {off,warn,error}` | パス系 USI オプションを事前検査する |
| `--engine KEY=VALUE ...` | エンジン設定を追加する（繰り返し指定可） |
| `--rules KEY=VALUE ...` | `rules.*` を上書きする |
| `--tournament KEY=VALUE ...` | `tournament.*` を上書きする |
| `--rating KEY=VALUE ...` | `rating.*` を上書きする |
| `--dashboard KEY=VALUE ...` | `dashboard.*` を上書きする |
| `--logging KEY=VALUE ...` | `logging.*` を上書きする |
| `--system KEY=VALUE ...` | `system.*` を上書きする |
| `--sprt KEY=VALUE ...` | `sprt.*` を上書きする |
| `--openbench KEY=VALUE ...` | `openbench.*` を上書きする |

YAML 設定の一部は、次のようにコマンドラインから上書きできます。

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

`run analyze` の主なオプションは次のとおりです。

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
| `--option KEY=VALUE` | engine option を上書きする（repeatable） |

`run mate` の主なオプションは次のとおりです。

| オプション | 説明 |
| --- | --- |
| `--ply-limit N` | 詰み探索の ply 上限 |
| `--node-limit N` | 詰み探索の node 上限 |
| `--infinite` | `go mate infinite` を使う |
| `--wait-bestmove` | mate result 後の trailing `bestmove` を待つ |
| `--timeout SEC` | mate result 待機の timeout |
| `--option KEY=VALUE` | engine option を上書きする（repeatable） |

## `dashboard`

```bash
shogiarena dashboard serve --run-dir /path/to/run
shogiarena dashboard serve --config tournament.yaml
shogiarena dashboard serve --run-dir /path/to/run --port 9090
```

`serve` は書き込みの終わったアーカイブを開きます。
追記中の CSA イベントログを追うには `watch` を使います。

```bash
shogiarena dashboard watch --csa-log-dir /path/to/csa/logs
shogiarena dashboard watch --csa-log-dir /path/to/csa/logs --port 9090 --out-run-dir ./csa-session
```

## `csa`

CSA プロトコルサーバーでの対局を観戦・書き出しします。詳細は
[CSA 対局の観戦](../user-guide/csa-watch.md) を参照してください。

```bash
shogiarena csa status --csa-log-dir /path/to/csa/logs
shogiarena csa status --csa-log-dir /path/to/csa/logs --follow
shogiarena csa export --csa-log-dir /path/to/csa/logs --out ./csa-records
```

`csa export` は全手を盤面に再生してから書きます。
再生できなかった対局は書かずに報告し、exit code は非ゼロになります。

## `results`

```bash
shogiarena results summary /path/to/run
shogiarena results summary /path/to/run --format json
shogiarena results summary /path/to/game.db --engine EngineA
shogiarena results verify-provenance /path/to/run
```

`summary --format json`の出力には、探索にかかった時間も含まれます。
`engine_wall_time_ms`は`think()`の呼び出しから`bestmove`の受信までの実時間、`wall_time_ms`は持ち時間から差し引いた時間です。
実時間あたりのNPSは、既定では`engine_wall_time_ms`を使って計算します。

## `replay-position`

保存済みの対局局面を、指定したエンジンで再探索します。

```bash
shogiarena replay-position --run-dir /path/to/run \
  --game-id g0001-abc \
  --ply 80 \
  --engine EngineA \
  --nodes 100000
```

USI通信ログを保存している場合は、その履歴を再現して探索することもできます。
