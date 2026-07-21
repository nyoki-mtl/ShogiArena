# トーナメント

トーナメントは `shogiarena run tournament` で実行します。
エンジン同士の比較は、まずこのモードで行います。

```bash
shogiarena run tournament tournament.yaml
```

## 主なオプション

| オプション | 説明 |
| --- | --- |
| `--dry-run` | 設定を読み込み、実行前に検証する |
| `--validate-only` | 設定検証のみで終了する |
| `--experiment-name NAME` | 自動生成される run グループ名を上書きする |
| `--run-dir PATH` | run ディレクトリを明示指定する |
| `--no-resume` | 既存状態を再開せず新規実行する |
| `--provision {none,force}` | SSH インスタンスへのエンジン配置を制御する |
| `--path-preflight {off,warn,error}` | USI オプション内のパスらしき値を事前検査する |

`--rules KEY=VALUE`、`--tournament KEY=VALUE`、`--dashboard KEY=VALUE` などで YAML の一部を CLI から上書きできます。

```bash
shogiarena run tournament tournament.yaml \
  --tournament games_per_pair=100 num_parallel=4 \
  --rules time_control.byoyomi_ms=1000
```

## 最小構成

```yaml
experiment_name: "engine-comparison"

engines:
  - engine_path: "engine_a.yaml"
  - engine_path: "engine_b.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 20
  num_parallel: 2

rules:
  time_control:
    time_ms: 60000
    increment_ms: 1000

dashboard:
  enabled: true
  api_port: 8080
```

## `engines`

エンジンは次のどちらかで指定します。

| 指定方法 | 用途 |
| --- | --- |
| `engine_path` | ローカルのエンジン YAML を参照する |
| `artifact` | リポジトリ定義とビルド設定からエンジンを解決する |

`engine_path` の例:

```yaml
engines:
  - engine_path: "examples/configs/resources/engines/local_example.yaml"
    name: "EngineA"
    options:
      Threads: 4
```

`artifact` の例:

```yaml
engines:
  - artifact: "YaneuraOu/9f89431a"
    build_options:
      target_cpu: ZEN3
    options:
      Threads: 4
      USI_Hash: 2048
```

トーナメント側で `options`、`options_overlays`、`time_control` を指定すると、参照先のエンジン設定に上書きマージされます。

## `tournament`

| フィールド | 既定値 | 説明 |
| --- | --- | --- |
| `scheduler` | `round_robin` | `round_robin` または `gauntlet` |
| `games_per_pair` | 4 | 1 ペアあたりの対局数 |
| `num_parallel` | 4 | 同時に走らせる対局数 |
| `seed` | 42 | スケジュールと局面選択の乱数シード |
| `game_order` | `auto` | `auto`, `pairwise`, `interleave`, `shuffle` |
| `engine_lifecycle` | `reuse` | `reuse` はエンジンプロセスを再利用、`per_game` は各対局後に終了 |
| `baseline_count` | 1 | `gauntlet` で先頭から何台を baseline にするか |

`gauntlet` は、先頭側の baseline 群と残りの候補群を重点的に対局させたい場合に使います。

`engine_lifecycle: per_game` を指定すると、各対局の `gameover` 後に両エンジンプロセスを閉じ、次局で新しい USI プロセスを起動します。
対局間でメモリ状態を持ち越したくない強さ比較で使います。
既定の `reuse` は、長いトーナメントの起動コストを抑えるためにプロセスを idle pool へ戻します。

### 並列数とインスタンス容量

`num_parallel` は「同時に実行したい対局数」として扱われます。
ShogiArena は各エンジンの `Threads` / `USI_Threads` と `Ponder` / `USI_Ponder` から必要 slot 数を見積もり、pending schedule の連続 `num_parallel` 局が instance の `slots` / `max_engines` に収まるかを対局開始前に検査します。

ponder off のエンジンは、片側につき `ceil(Threads / 2)` slot を予約します。
たとえば `Threads: 4` のエンジン同士は 1 局で 4 slot を使うため、`num_parallel: 3` には同じ instance 上で 12 slot が必要です。
instance が `slots: 8` の場合は既定でエラーになります。

instance resource gate の側で並列数を絞りたい場合だけ、`system.resource_capacity_preflight` を変更します。

```yaml
system:
  resource_capacity_preflight: error  # default: error, warn, off
```

## `rules`

### 時間制御

主な指定:

- `fixed_time_ms`: 1 手固定時間
- `time_ms` + `increment_ms`: フィッシャー式
- `time_ms` + `byoyomi_ms`: 秒読み
- `node_limit`: ノード数制限
- `depth_limit`: 深さ制限

### 初期局面

```yaml
rules:
  initial_positions:
    type: file
    source: "openings/startpos.sfen"
    flip_policy: pair_both
```

`type` は `startpos` または `file` です。
`file` の場合は SFEN または opening line のリストを `source` に指定します。
`shogiarena run tournament` / `run sprt` で YAML ファイルから実行する場合、相対 `source` はその YAML ファイルの場所を基準に解決されます。

`source_format` は `auto`、`sfen`、`usi_line` を指定できます。

| 値 | 意味 |
| --- | --- |
| `auto` | 行の形から SFEN / USI line を自動判定する |
| `sfen` | 各行を初期局面 SFEN として読む |
| `usi_line` | 各行を `7g7f 3c3d ...` のような USI 指し手列として読み、line 適用後の SFEN を初期局面にする |

pair-synchronized opening line として使う場合、ペア同期の機構は `flip_policy: pair_both` です。
同じ file entry から得た同じ初期 SFEN を、先後入れ替えの 2 局へ渡します。
`sync_scope: pair` は同期を有効化する独立スイッチではなく、「この設定は pair 同期を意図している」と明示し、`flip_policy: pair_both` 以外なら設定検証で止める guard です。

```yaml
rules:
  initial_positions:
    type: file
    source: "openings/pair_lines.usi"
    source_format: usi_line
    flip_policy: pair_both
    sync_scope: pair
    preserve_line_metadata: true
```

`preserve_line_metadata: true` にすると、opening line の source path、line number、line id、USI moves が record metadata に保存され、Dashboard の Book / Pairs report でも pair 診断に使えます。
line file は空でない行をすべて entry として読むため、コメント行は入れず、SFEN または USI line だけを書いてください。

ShogiArena-managed opening line は、対局開始前に局面を作るだけです。
line の指し手を棋譜へ強制挿入せず、保存される指し手は initial SFEN 以降にエンジンが実際に指した手だけです。
ただし engine book を有効にしたままだと、同期 line 後の局面からさらにエンジン内蔵定跡が続くことがあります。
純粋な強さ比較では、engine-owned book と混ざらないようエンジン側の `BookFile: no_book` を推奨します。

### adjudication

```yaml
rules:
  adjudication:
    enable_max_plies: true
    max_plies: 320
    sync_max_plies_with_engine: true
    resign_threshold_cp: 800
    resign_move_count: 8
```

最大手数、投了判定、エンジン側の引き分け手数オプション同期を設定できます。

## 実行結果

`--run-dir` を指定した場合は、そのパスが run ディレクトリとして使われます。
指定しない場合は、標準出力先に次の形で作られます。

```text
{output_dir}/tournament/runs/<experiment>-<hash8>/YYYYMMDDHHMMSS/
├── game.db
├── manifest.json
├── state.json
├── data/
├── records/
└── transcripts/
```

結果集計:

```bash
shogiarena results summary /path/to/run
shogiarena results summary /path/to/run --format csv
```

### timing metrics

各指し手には 2 種類の wall time が記録されます。

| フィールド | 意味 |
| --- | --- |
| `wall_time_ms` | 持ち時間管理で課金された wall time |
| `engine_wall_time_ms` | `think()` 呼び出しから `bestmove` 回収までの engine I/O 窓 |

`engine_wall_time_ms` は `game.db` の `game_move.engine_wall_time_ms`、live/detail payload の `engine_wall_times_ms`、`results summary --format json` の timing metadata から確認できます。
engine throughput や wall NPS を比較するときの既定 field は `engine_wall_time_ms` です。

provenance の検証:

```bash
shogiarena results verify-provenance /path/to/run
```

## 関連

- [エンジン設定](engine-configuration.md)
- [ダッシュボード](dashboard.md)
- [リモート実行](remote-execution.md)
