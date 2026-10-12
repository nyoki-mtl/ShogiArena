# エンジン設定

ShogiArena は USI エンジンを YAML で定義します。
トーナメント設定の `engines` からこの YAML を参照するか、`artifact` を直接指定します。

## ローカルエンジン

実行ファイルと、評価関数や定跡の配置に合わせて設定します。
Linux や macOS では、実行ファイルに実行権限を付けてください。

```yaml
name: "Local Engine"
engine_path: "/path/to/engine"
working_directory: "/path/to"
engine_args:
  - "--usi"
environment:
  OMP_NUM_THREADS: "2"
options:
  Threads: 2
  USI_Hash: 256
go_options:
  nodes: 1000000
enable_early_ponder: false
handshake_timeout: 10
io:
  collect_info_strings: false
  collect_raw_io: true
  collect_stderr: true
  collect_outbound: true
option_validation:
  default: strict
  overrides:
    BookFile: allow_unlisted_combo_value
```

主なフィールド:

| フィールド | 説明 |
| --- | --- |
| `name` | 表示名。省略時は `engine_path` などから派生 |
| `engine_path` | USI エンジン実行ファイル |
| `working_directory` | エンジンの作業ディレクトリ |
| `engine_args` | エンジン起動時の引数 |
| `environment` | エンジンプロセスへ渡す環境変数 |
| `options` | USI `setoption` として送る値 |
| `go_options` | `go depth` / `go nodes` へ渡すエンジン単位の既定値 |
| `enable_early_ponder` | ponderhit を早めに送る実験的オプション |
| `handshake_timeout` | 起動、`usiok`、`readyok`、停止回収などの待機秒数 |
| `io` | info string / raw USI I/O / stderr / outbound command の収集方針 |
| `option_validation` | USI option 値の検証方針 |

`engine_path`、`working_directory`、`options` 内のパス値には相対パスや `{engine_dir}` を使えます。
`go_options` は `depth` と `nodes` のみを受け付けます。
`movetime`、`btime`、`wtime`、`byoyomi`、`infinite` などの時間制御は、対局中の持ち時間管理と競合するためエンジン設定では指定できません。
時間制御は run 設定の `rules.time_control` で指定してください。

## 入出力の記録とオプションの検証

USI コマンドやエンジンの出力を記録するには、`io` を指定します。

```yaml
io:
  collect_info_strings: true   # UsiThinkResult.info_strings に info line を残す
  collect_raw_io: true         # stdin/stdout の typed UsiIoEvent を発行する
  collect_stderr: true         # stderr も UsiIoEvent(direction="stderr") として扱う
  collect_outbound: true       # ShogiArena から送った command も記録する
```

USI option は既定で `strict` に検証されます。
mode は `strict`、`warn`、`raw`、`allow_unlisted_combo_value` です。
`allow_unlisted_combo_value` は、combo option の候補一覧に出ないファイル名を渡す必要がある場合だけ option 単位で指定してください。

```yaml
option_validation:
  default: strict
  overrides:
    BookFile: allow_unlisted_combo_value
```

### 詰み探索と起動待機の設定

必要に応じて、次の項目もエンジン YAML に指定できます。

| フィールド | 用途 |
| --- | --- |
| `mate_default_ply_limit` | `run mate` / Python mate search の既定 ply 上限 |
| `mate_default_node_limit` | mate search の既定 node 上限 |
| `mate_default_infinite` | mate search の既定を `go mate infinite` にする |
| `mate_wait_for_bestmove` | mate result 後の trailing `bestmove` を待つ |
| `isready_sync_strategy` | `isready` 同期の方式。`direct`、`wait`、`stop` |
| `isready_lock_key` / `isready_lock_template` | option 値から isready lock key を作る |
| `isready_lock_check_key` / `isready_lock_check_template` / `isready_lock_check_templates` | lock 解放確認に使う file path template |
| `isready_lock_skip_if_exists` | lock file が既にある場合に待機を省略する |

## 内蔵定跡

YaneuraOu 系エンジンの `BookDir` / `BookFile` は、エンジン内蔵定跡として扱います。
ShogiArena は定跡ファイルを検証して出所を記録し、USI オプションとしてエンジンへ渡します。
定跡から指し手を選ぶのはエンジンです。

```yaml
options:
  USI_OwnBook: true
  BookDir: "./book"
  BookFile: "user_book1.db"
  BookOnTheFly: true
```

`USI_OwnBook` が明示的に `false` でなく、`BookFile` が `no_book` でない場合、`BookDir + BookFile` の実体パスを起動前に検証します。
大きな YANEURAOU-DB2016 形式の book では `BookOnTheFly: true` を使う運用を推奨します。

`BookFile: user_book1.db` の `.db` がなく、同じ場所に `user_book1.ybb` がある場合は、`.ybb` を定跡ファイルとして使います。
元の指定と実際に使ったパスを記録し、リモート実行でも `.ybb` を配布します。

エンジンが `BookFile` を combo option として宣言していて、任意の book ファイル名を `var` に列挙しない場合は、上の `option_validation.overrides.BookFile` で `allow_unlisted_combo_value` を指定します。
これは値の送信を許可するだけで、ShogiArena の path preflight と provenance 記録は引き続き実行されます。

各指し手には、着手の由来を示す `move_source` と、定跡候補手に含まれるかを示す `book_hit` が記録されます。
ShogiArena が通常の USI 出力から観測できる範囲では、book を示す info string があれば `book`、探索統計があれば `search`、断定できない手は `unknown` です。
`search` は探索統計を観測したという意味であり、book 由来ではないことの断定ではありません。
`book_hit` は book lookup などで明示的に判定された場合だけ入り、未測定は `null` のままです。

実力比較や SPSA でエンジンごとに異なる内蔵定跡を使うと、定跡の違いも結果に影響します。
定跡を使わずに比較するには、`BookFile: no_book` または `USI_OwnBook: false` を指定してください。
サンプルのオプション上書き用 YAML では `BookFile: no_book` を使っています。

共通の開始局面や、先後を入れ替えて同じ手順を使う設定は、`rules.initial_positions` で指定します。
指し手列は対局前に初期局面の SFEN へ変換され、棋譜には挿入されません。
内蔵定跡を有効にしている場合は、その初期局面からさらに定跡手が続くことがあります。

## artifact エンジン

ビルド済み成果物やリポジトリ定義からエンジンを解決する場合は `artifact` を使います。

```yaml
name: "YO-mainline"
artifact: "YaneuraOu/9f89431a"
build_options:
  target_cpu: ZEN3
  edition: YANEURAOU_ENGINE_NNUE_HALFKP_512X2_8_64
options:
  Threads: 4
  USI_Hash: 2048
```

`artifact` と `engine_path` は同時に指定できません。
`artifact` を使う場合、通常は `build_options.target_cpu` が必要です。

## トーナメント側からの上書き

同じエンジン YAML を使いながら、トーナメントごとに名前や USI オプションを変えられます。

```yaml
engines:
  - engine_path: "engines/yaneuraou.yaml"
    name: "YaneuraOu_Strong"
    options:
      Threads: 4
      USI_Hash: 1024
  - engine_path: "engines/yaneuraou.yaml"
    name: "YaneuraOu_Light"
    options:
      Threads: 1
      USI_Hash: 128
```

`options_overlays` を使うと、USI オプション上書き用 YAML を段階的にマージできます。
overlay YAML は必ず `options:` ブロック配下に書きます。

```yaml
engines:
  - artifact: "YaneuraOu/9f89431a"
    build_options:
      target_cpu: ZEN3
    options_overlays:
      - "examples/configs/resources/engines/overlays/YaneuraOu.yaml"
    options:
      Threads: 4
```

overlay YAML の例:

```yaml
options:
  USI_Hash: 1024
  BookFile: no_book
```

## 動作確認

単体エンジンの起動確認:

```bash
shogiarena run analyze engine.yaml startpos --nodes 100000
shogiarena run mate engine.yaml startpos --ply-limit 5
```

トーナメント設定込みの検証:

```bash
shogiarena run tournament tournament.yaml --dry-run
```

## サンプル

- `examples/configs/resources/engines/local_example.yaml`
- `examples/configs/resources/engines/artifact_example.yaml`
- `examples/configs/resources/engines/overlays/`
