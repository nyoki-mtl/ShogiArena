# トーナメントの実行

Shogi Arena でエンジン同士のトーナメント（対局）を実行する方法を解説します。

## 実行コマンド

トーナメントは `shogiarena run tournament` コマンドで実行します。

```bash
shogiarena run tournament <config_path> [options]
```

### 主なオプション

| オプション | 説明 |
| --- | --- |
| `--dry-run` | 実際に対局を行わず、設定の検証とスケジュール生成のみを行います。 |
| `--validate-only` | 設定だけ検証して終了します。 |
| `--no-resume` | 既存ランの再開をせず新規として実行します。`--run-dir` で既存ディレクトリを指定した場合のみ、そのディレクトリ内の既存成果物が上書きされます。 |
| `--engine KEY=VALUE ...` | エンジンをCLIで定義します（複数指定可）。`instance_id=...` を含めることで割当も可能です。 |
| `--provision {none,force}` | SSH リモート実行時にファイルを同期するか制御します。`force` は毎回同期します。 |
| `--git-worktree {strict,clean,allow-dirty}` | ビルド前の Git ワークツリーの状態チェックを制御します。 |
| `--experiment-name NAME` | 自動生成する run グループ名を上書きします。 |
| `--run-dir PATH` | run ディレクトリを明示指定します。 |

### 実行ディレクトリの決まり方

- `--run-dir` を指定した場合は **そのパスがそのまま使われます**（`<experiment-name>-<hash8>/<timestamp>` は付かない）
- `--run-dir` を指定しない場合は、`output_dir` 配下に  
  `runs/<experiment-name>-<hash8>/<timestamp>` が作成されます
- `--experiment-name` は **run_dir を自動生成する場合のみ** 反映されます

`--run-dir` は相対パスも指定でき、`{output_dir}` や環境変数を展開できます。

## 設定ファイル (TournamentRunConfig)

トーナメントの設定は YAML ファイルで記述します。

### 基本構造

```yaml
experiment_name: "my_experiment"  # 実験名（ディレクトリ名などに使用）

engines:
  - name: "EngineA"
    engine_path: "configs/engine/engine_a.yaml"
  - name: "EngineB"
    engine_path: "configs/engine/engine_b.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 100
  num_parallel: 4

rules:
  time_control:
    time_ms: 60000     # 60秒
    increment_ms: 1000 # +1秒
  initial_positions:
    type: file
    source: "configs/openings/standard.sfen"

dashboard:
  enabled: true
  api_port: 8080

instances:
  - configs/resources/instances/local.yaml
```

### Engines (`engines`)

参加するエンジンをリストで定義します。

| フィールド | 説明 |
| --- | --- |
| `name` | エンジンの表示名（一意である必要があります）。 |
| `engine_path` | ローカルのエンジン設定 YAML へのパス。[詳細](engine-configuration.md) |
| `artifact` | `engine_path` の代わりにビルド済みアーティファクト（例: `YaneuraOu/<commit>`）を指定。[詳細](build-system.md) |
| `build_options` | `artifact` 使用時のビルドオプション（必要なものだけ指定）。 |
| `options` | USI オプションの上書き設定。 |
| `time_control` | このエンジン固有の持ち時間設定（`rules` より優先されます）。 |
| `cpu_affinity` | CPU コアの固定（例: `"0,2-3"`）。 |
| `instance_id` | 実行先インスタンス名。SSH 分散実行時に使います。 |

### Tournament (`tournament`)

対局のスケジュール方法を定義します。

| フィールド | デフォルト | 説明 |
| --- | --- | --- |
| `scheduler` | `round_robin` | `round_robin`（総当たり）または `gauntlet`（勝ち抜き戦）。 |
| `games_per_pair` | 4 | 1ペアあたりの対局数。 |
| `num_parallel` | 4 | 同時実行する対局数。 |
| `game_order` | `auto` | 対局順序。`interleave`（交互）、`pairwise`（連続）、`shuffle`（ランダム）。 |
| `seed` | 42 | スケジュール生成や局面選択の乱数シード。 |
| `baseline_count` | 1 | `gauntlet` 時の基準エンジンの数（リストの先頭から）。 |

### Rules (`rules`)

対局のルールを定義します。

#### Time Control (`time_control`)

- `time_ms`: 基本持ち時間（ミリ秒）
- `increment_ms`: 1手ごとの加算時間（フィッシャー）
- `byoyomi_ms`: 秒読み時間
- `fixed_time_ms`: 1手固定時間
- `node_limit`: 探索ノード数制限
- `depth_limit`: 探索深さ制限

#### Initial Positions (`initial_positions`)

- `type`: `startpos`（平手）または `file`（指定局面）。
- `source`: `type: file` の場合の SFEN ファイルパス。
- `flip_policy`: 先後入れ替え設定。`pair_both`（入れ替えて2局）、`random`、`alternate`。

詳細は[開局集の管理](opening-books.md)を参照してください。

#### Adjudication (`adjudication`)

- `enable_max_plies`: 最大手数による引き分け判定を有効化（デフォルト True）。
- `max_plies`: 最大手数（デフォルト 320）。
- `sync_max_plies_with_engine`: エンジンの引き分け手数オプションを同期するか（デフォルト True）。
- `engine_max_ply_option_names`: 同期対象のエンジンオプション名（`"auto"` で自動検出）。
- `resign_threshold_cp`: 投了判定スコア（例: 800）。`None` で投了無効、非 `None` で有効。
- `resign_move_count`: 連続何手で投了するか。

#### Repetition (`repetition_occurrences_to_draw`)

同一局面が何回現れたら千日手とみなすか（デフォルト 2、公式ルールは 4）。

### Rating (`rating`)

Elo レーティング計算の設定です。

- `initial`: 初期レーティング（デフォルト 1500）。
- `k_factor`: K値（デフォルト 16）。

### Dashboard (`dashboard`)

- `enabled`: ダッシュボードを有効にするか。
- `api_port`: ポート番号。

### USI Transcript (`logging`)

対局中の USI command/output を game/side ごとに保存できます。デバッグ用なので、必要な run だけで有効にしてください。

```yaml
logging:
  usi_transcript: true
  usi_transcript_detail: commands  # commands | commands_and_info
```

出力先は `<run_dir>/transcripts/game-<game_id>-black.log` と `...-white.log` です。`commands` は送信コマンド、`bestmove`、最後の non-bound `info` を保存し、`commands_and_info` は全 `info` 行も保存します。

保存済み局面は `replay-position` で再検索できます。

```bash
shogiarena replay-position --run-dir path/to/run --game-id g0001-abc --ply 80 --engine EngineA --nodes 100000
```

`--fresh` は対象局面だけを clean state で検索します。`--replay-history` は transcript がある場合に、同じ side の過去 search を対象 ply 手前まで再実行してから検索します。

### Instances (`instances`)

インスタンス設定ファイルのパスを指定します。1 ファイルでも複数ファイルでも指定できます。

```yaml
instances:
  - configs/resources/instances/local.yaml
  - configs/resources/instances/gpu.yaml
```

### Records Output (`records_output`)

自己対局の棋譜を psv/sbinpack 形式でバイナリ保存する設定です。ファイルの切り替えは**対局境界のみ**で行います。

```yaml
records_output:
  format: sbinpack
  max_positions_per_file: 1000000
  max_games_per_file: 2000
  output_dir: .sandbox/work_dir/records
  file_prefix: selfplay
```

#### フィールド

- `format`: `psv` または `sbinpack`
- `max_positions_per_file`: 1ファイルあたりの局面数上限
- `max_games_per_file`: 1ファイルあたりの対局数上限（sbinpackのみ）
- `output_dir`: 出力先ディレクトリ（省略時は run_dir/records）
- `file_prefix`: 出力ファイルの接頭辞（省略時は format 名）

> **Note: 境界の扱い**
>
> sbinpack は対局途中でファイルを分割しません。`max_games_per_file` を指定した場合は対局数で固定し、未指定の場合は `max_positions_per_file` を超過した時点の対局まで書き込んでから次ファイルへ切り替えます。psv は1局面=1レコードのため `max_games_per_file` は使用できません。


## ディレクトリ構造

実行結果は `output_dir`（デフォルトは `~/.local/share/shogiarena/output` など）以下に保存されます。

```text
output_dir/
└── tournament/
    └── runs/
        └── <config_stemまたはexperiment_name>-<hash8>/
            └── YYYYMMDDHHmmSS/
                ├── game.db         # 結果データベース
                ├── state.json      # 進行状態（レジューム用）
                ├── manifest.json   # run マニフェスト（provenance シール）
                ├── data/           # ダッシュボード用データ
                ├── logs/           # 実行ログ
                └── records/        # records_output を使う場合
```

`output_dir` の設定については[設定システム](configuration.md)を参照してください。

## OpenBench/ShogiBench への提出

SPRT 実行時は、`openbench.enabled: true` を設定すると OpenBench/ShogiBench へ進捗を提出できます。

```yaml
sprt:
  elo0: 0.0
  elo1: 5.0
  alpha: 0.05
  beta: 0.05
  max_games: 400

openbench:
  enabled: true
  mode: existing_test
  server: https://your-openbench.example.com
  username: your-user
  password_env: OPENBENCH_PASSWORD
  target_test_id: 1234
  submit_interval_games: 2
  strict: true
```

### 前提条件

- `sprt` 設定が必須です
- エンジン数は 2 台固定です
- 1 台目のエンジンを tested、2 台目を base として集計します
- `target_test_id` で提出先の test を指定します
- `mode: create_test` を使うと test 作成（CREATE_TEST）から自動化できます

### 実行時の挙動

- `submit_interval_games` ごとに差分を送信します
- 実行終了時に最終 flush を行います
- `strict: true` の場合、提出失敗で run をエラー終了します
- `strict: false` の場合、提出失敗を警告ログに落として run は継続します

### 認証情報

- `password_env` は環境変数名です（`settings.yaml` に平文パスワードは保存しません）
- `server` はデフォルトで `https://` 必須です（`allow_insecure_http: true` で `http://` を許可）

## 関連ドキュメント

- [エンジン設定](engine-configuration.md): エンジン設定ファイルの詳細
- [開局集の管理](opening-books.md): 初期局面の設定
- [リモート実行](remote-execution.md): SSH 経由の分散実行
- [設定システム](configuration.md): 環境設定とプレースホルダー
