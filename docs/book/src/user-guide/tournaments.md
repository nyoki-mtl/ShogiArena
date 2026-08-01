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

### 完了状態

run が最後まで走ったかどうかは `completion_status.json` で判定します。

この artifact は、失敗しうる最終処理と後始末がすべて終わった後にだけ書かれます。
`completed.flag` は「その確定書き込みが済んだ」ことを示す派生マーカーで、単独では成功の証拠になりません。

```json
{
  "schema_version": 1,
  "status": "clean",
  "termination_reason": "sprt-finished",
  "scheduled": 1000,
  "completed": 124,
  "cancelled": 8,
  "not_played": 868,
  "error_games": 0,
  "timeouts_by_origin": { "engine_deadline": 5 },
  "coverage_incomplete_timeouts": 0,
  "watchdog": {
    "loop_lag_events": 3,
    "thread_lag_events": 0,
    "max_loop_lag_ms": 412.0,
    "max_thread_lag_ms": 0.0,
    "loop_threshold_ms": 200.0,
    "thread_threshold_ms": 200.0,
    "dropped_loop_events": 0,
    "is_coverage_complete": true
  }
}
```

`status` と `termination_reason` は必ず対で読んでください。

`status` の値は次の 3 つです。

- **clean**：正常に終了し、無効局も出ていません。
- **with-anomalies**：正常に終了しましたが、無効局（`ERROR`）や原因を断定できない時間切れが含まれます。
- **failed**：**この run の結果を完了した測定として扱えない**、という意味です。

`failed` は異常終了を意味しません。
利用者が意図して停止した run も `failed` になります。
実際に何が起きたかは `termination_reason` を読んでください。

| `termination_reason` | `status` | 意味 |
| --- | --- | --- |
| `schedule-complete` | `clean` / `with-anomalies` | 予定した対局をすべて消化した |
| `sprt-finished` | `clean` / `with-anomalies` | SPRT が正常に結論へ到達して早期終了した（下記の「標本を締めるタイミング」を参照） |
| `cancelled` | `failed` | 利用者が停止した。故障ではない |
| `timeout-burst` | `failed` | ShogiArena 側の停滞に起因する時間切れが閾値に達した |
| `timeout-attribution-unknown` | `failed` | 原因を断定できない時間切れが閾値に達した |
| `transport-timeout` | `failed` | 通信・プロトコル待ちの失敗が閾値に達した |
| `incomplete` | `failed` | 正常終了の証拠がないまま予定を消化できていない |
| `runtime-error` | `failed` | 実行中のエラーで終了した |
| `finalization-error` | `failed` | 最終処理に失敗した |
| `cleanup-error` | `failed` | 後始末に失敗した |

`not_played` は「予定したが実施しなかった」局数です。
正常な早期終了でもこの値は増えます。
上の例では 1000 局を予定して SPRT が 124 局で決着したため、`not_played` が 868 でも `status` は `clean` です。

中断された run では、後始末の前に暫定の status を書いてから確定へ昇格させます。
暫定のまま残った場合は `is_provisional` が `true` になります。
これは「run が中断され、後始末の結果を反映できていない」という意味で、`status` と
`termination_reason` はそのまま読んで構いません。
後始末そのものに失敗した場合は `cleanup_error` が併記されます。
中断の理由（`cancelled` など）が後始末の失敗で置き換わることはありません。

#### SPRT が標本を締めるタイミング

SPRT は結論に達した時点で標本を締めます。

停止を決めたときに実行中だった対局は、そのまま最後まで進み、`game.db` と棋譜には記録されます。
ただし検定へは加えません。
検定の停止条件は「停止した時点の標本」の関数である必要があり、後から到着した対局を足すと
一度出た結論が取り消されてしまうためです。

締めた後に完了した局数は、ダッシュボードの SPRT 状態と API 応答の `late_games` で確認できます。
`completed` と実際の対局数が食い違って見える場合は、この値を確認してください。

なお、`sprt.min_games` に達するまでは結論に到達していても停止しません。
標本を締めるのも実際に停止を決めたときです。

`watchdog` は、実行中に検出した event loop とスレッドの停滞の集計です。
`max_loop_lag_ms` が持ち時間に対して無視できない大きさなら、その run の計測値は負荷の影響を受けています。
`is_coverage_complete` が `false` の場合、監視記録の一部が失われているため、由来判定の一部が `unknown` に倒れます。

`completion_status.json` が存在しない場合は、`manifest.json` の `shogiarena_version` を併せて確認してください。
1.0.x の run はこの artifact を持ちません。
1.1.0 以降の run で存在しない場合は、最終処理に到達する前に中断された可能性があります。

### 時間切れの由来

時間切れには、エンジンが実際に持ち時間を超過した場合と、ShogiArena 側の停滞で超過したように見えた場合があります。
後者をエンジンの負けとして記録すると、レーティングと SPRT に実力とは無関係な差が混入します。

判定には次の 3 つを使います。

1. その手の締切（持ち時間から確定した deadline）
2. ShogiArena が `bestmove` を最初に観測した時刻
3. その時間窓を watchdog が観測できていたか

締切の超過分が、同じ窓に重なった停滞より大きい場合だけ、エンジン起因と判定します。
観測が遅れた分をすべて停滞のせいだと仮定しても、実際の到着が締切より後になるからです。
逆に、停滞だけで超過を説明できてしまう場合は原因を確定できないため、勝敗を付けません。

判定結果は `timeouts_by_origin` に由来別の件数として現れます。

- **engine_deadline**：エンジン起因です。従来どおり時間切れ負けとして記録し、レーティングと SPRT に算入します。
- **orchestrator_stall**：締切より前に `bestmove` を観測できていて、その後の処理だけが遅れた場合です。勝敗を付けず `ERROR`（無効局）として記録します。
- **unknown**：**原因を断定できなかった**場合です。停滞だけで超過を説明できてしまう場合、watchdog がその窓を観測できていなかった場合（記録の溢れ、watchdog の再起動）、リモート実行のように出力の到達時刻を保証できない場合が該当します。無効局として扱い、標本から除外します。
- **transport_timeout**：持ち時間の予算を持たない探索設定（`depth` / `nodes` のみ）での待ち失敗や、プロトコル待ちの失敗です。無効局として扱います。
- **unattributed**：停滞の計測自体がない実行（SPSA など）です。保守的に従来どおりの時間切れ負けとして扱います。

`unknown` は「ShogiArena が悪い」でも「エンジンが悪い」でもなく、**どちらとも言えない**という意味です。
片側に倒すと標本が偏るため、勝敗を付けずに除外します。

無効局が出ても run はすぐには止まりません。
由来ごとの閾値に達したときにだけ新規対局の投入を止め、`status` を `failed` にします。
閾値未満なら run は続き、当該局が標本から除かれるだけです（`status` は `with-anomalies` になります）。

除外率は `timeouts_by_origin` と `completed` から後から検証できます。
無効局の比率が無視できない水準なら、その run の結論は保留してください。

なお、この判定を有効にしているのは tournament / SPRT 系の実行だけです。
SPSA は従来どおりの扱いを維持します。

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
