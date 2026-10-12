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
| `--provision {cas,preplaced}` | SSH resourceをCAS配置またはpath/digest検証済み既配置として扱う |
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

ponderが無効、または指定されていない場合は、片側につき`ceil(Threads / 2)`スロットを予約します。
ponderが有効な場合は、片側につき`Threads`と同じ数のスロットを予約します。
たとえば `Threads: 4` のエンジン同士は 1 局で 4 slot を使うため、`num_parallel: 3` には同じ instance 上で 12 slot が必要です。
instance が `slots: 8` の場合は既定でエラーになります。

容量に合わせて実行時に並列数を制限したい場合は、`system.resource_capacity_preflight` を `warn` または `off` に変更します。

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

`selection_policy` の既定値は `with_replacement` で、同じ行を複数回選ぶことがあります。
`without_replacement` にすると schedule 内で各行を一度だけ選びます。
先後入れ替えの 2 局に同じ行を使う `flip_policy: pair_both` では、その 2 局を 1 回の選択として数えます。
必要な選択回数よりファイルの行数が少ない場合は、対局開始前にエラーになります。

`source_format` は `auto`、`sfen`、`usi_line` を指定できます。

| 値 | 意味 |
| --- | --- |
| `auto` | 行の形から SFEN / USI line を自動判定する |
| `sfen` | 各行を初期局面 SFEN として読む |
| `usi_line` | 各行を `7g7f 3c3d ...` のような USI 指し手列として読み、line 適用後の SFEN を初期局面にする |

先後を入れ替えた 2 局を同じ開始局面で指すには、`flip_policy: pair_both` を指定します。
`sync_scope: pair` を併せて指定すると、`flip_policy` が `pair_both` であることを設定検証で確認します。

```yaml
rules:
  initial_positions:
    type: file
    source: "openings/pair_lines.usi"
    source_format: usi_line
    selection_policy: without_replacement
    flip_policy: pair_both
    sync_scope: pair
    preserve_line_metadata: true
```

`preserve_line_metadata: true` にすると、指し手列のファイルパス、行番号、行 ID、USI 指し手列を対局記録に保存します。
ダッシュボードの Book タブの `Pairs` でも、ペアの開始手順を確認できます。
ファイルの空でない行はすべて開始局面として読むため、コメント行は入れず、SFEN または USI 指し手列だけを書いてください。

指定した指し手列は、対局開始前に初期局面を作るために使います。
棋譜に保存する指し手は、その初期局面からエンジンが実際に指した手だけです。
エンジン内蔵定跡が有効な場合は、開始局面からさらに定跡手が続くことがあります。
定跡の影響を除いて強さを比較するには、エンジン側の `BookFile: no_book` を推奨します。

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
├── completed.flag
├── completion_status.json
├── data/
├── records/
└── transcripts/
```

結果集計:

```bash
shogiarena results summary /path/to/run
shogiarena results summary /path/to/run --format csv
```

### 終了状態

`completion_status.json`の`status`と`termination_reason`で、正常に完了したか、途中で停止したかを確認できます。
`completed.flag`があっても、それだけで正常完了とは判断できません。

| `status` | 意味 |
| --- | --- |
| `clean` | 正常に終了し、無効局も出ていない |
| `with-anomalies` | 正常に終了したが、無効局や原因不明の時間切れを含む |
| `failed` | 完了した測定として扱えない。利用者が停止した場合も含む |

停止の理由は`termination_reason`に記録されます。

| 値 | 意味 |
| --- | --- |
| `schedule-complete` | 予定した対局をすべて消化した |
| `sprt-finished` | SPRTが結論に達して早期終了した |
| `cancelled` | 利用者が停止した |
| `timeout-burst` | ShogiArenaの停滞に起因する時間切れが閾値に達した |
| `timeout-attribution-unknown` | 原因不明の時間切れが閾値に達した |
| `transport-timeout` | 通信やプロトコルの待機失敗が閾値に達した |
| `incomplete` | 予定を消化しておらず、正常終了も確認できない |
| `runtime-error` | 実行中にエラーが起きた |
| `finalization-error` | 最終処理に失敗した |
| `cleanup-error` | 後始末に失敗した |

SPRTの早期終了では、予定より少ない対局数でも正常完了になります。
たとえば、1000局を予定して124局で検定が決着した場合は、次のように記録されます。

```json
{
  "status": "clean",
  "termination_reason": "sprt-finished",
  "scheduled": 1000,
  "completed": 124,
  "cancelled": 8,
  "not_played": 868
}
```

`not_played`は、予定していたが実施しなかった局数です。
`completion_status.json`がない場合は、完了した測定として扱う前に実行ログを確認してください。

### SPRT終了後の対局

SPRTは停止を決めた時点で集計を止めます。
そのとき実行中だった対局は最後まで進み、DBや棋譜には保存されますが、検定には追加しません。
この局数はダッシュボードのSPRT状態やAPIの`late_games`で確認できます。
未開始だった局は`not_played`に残ります。
`sprt.min_games`に達するまでは、LLRが境界を超えても停止しません。

### 時間切れと無効局

エンジンの時間切れと、ShogiArenaや通信の問題による待機失敗を区別して記録します。
`completion_status.json`の`timeouts_by_origin`に、それぞれの件数が入ります。

| 値 | 対局結果の扱い |
| --- | --- |
| `engine_deadline` | エンジンの時間切れ負け。レーティングとSPRTに算入する |
| `orchestrator_stall` | ShogiArena側の処理の遅れ。無効局として除外する |
| `unknown` | 原因を断定できない時間切れ。無効局として除外する |
| `transport_timeout` | 通信やプロトコルの待機失敗。無効局として除外する |
| `unattributed` | 停滞の計測がない実行。時間切れ負けとして扱う |

無効局が出ても、設定した閾値に達するまでは対局を続けます。
この場合の終了状態は`with-anomalies`です。
閾値に達すると新規対局を止め、終了状態を`failed`にします。
無効局が多い結果は、強さの比較に使う前に原因を確認してください。

この時間切れの分類はトーナメントとSPRTで使います。
SPSAの時間切れは`unattributed`として扱います。
失敗した局の詳細は`failures/run_failures.jsonl`、エンジン起動失敗は`failures/engine_startup_failure.json`で確認できます。

### 指し手ごとの実測時間

各指し手には 2 種類の時間が記録されます。

| フィールド | 意味 |
| --- | --- |
| `wall_time_ms` | 持ち時間から差し引かれた時間 |
| `engine_wall_time_ms` | `think()` 呼び出しから `bestmove` 回収までの実測時間 |

`engine_wall_time_ms` は、`game.db` の `game_move.engine_wall_time_ms` や、`results summary --format json` の時間情報から確認できます。
エンジンの処理速度や wall NPS を比較するときは、`engine_wall_time_ms` を使います。

使用したエンジンやファイルの検証:

```bash
shogiarena results verify-provenance /path/to/run
```

## 関連

- [エンジン設定](engine-configuration.md)
- [ダッシュボード](dashboard.md)
- [リモート実行](remote-execution.md)
