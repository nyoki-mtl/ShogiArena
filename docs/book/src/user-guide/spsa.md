# SPSA Tuning

Shogi Arena は SPSA (Simultaneous Perturbation Stochastic Approximation) アルゴリズムを用いたエンジンパラメータの自動チューニングをサポートしています。

## 実行コマンド

`run` コマンドの `spsa` サブコマンドで実行します。

```bash
shogiarena run spsa configs/run/spsa/example.yaml [options]
```

### 実行ディレクトリの決まり方

- `--run-dir` を指定した場合は **そのパスがそのまま使われます**（`<experiment-name>-<hash8>/<timestamp>` は付かない）
- `--run-dir` を指定しない場合は、`output_dir` 配下に
  `spsa/runs/<experiment-name>-<hash8>/<timestamp>` が作成されます
- `--experiment-name` は **run_dir を自動生成する場合のみ** 反映されます

`--run-dir` は相対パスも指定でき、`{output_dir}` や環境変数を展開できます。

## 設定ファイル (SpsaRunConfig)

SPSA 用の設定ファイルはトップレベルに `spsa` ブロックを持ちます。チューニング対象のパラメータ自体は
`spsa` ブロックに直接書くのではなく、**SPSA space spec** という別ファイルに切り出して `spsa.space` から参照します
（[SPSA space protocol](#spsa-space-protocol) を参照）。

### 基本構造

```yaml
engines:
  - artifact: "rshogi-az/local"
    options:
      Threads: 1
      USI_Hash: 256
    # 1 つのディスクリプタで十分。SPSA が plus/minus バリアントを自動生成します。

dashboard:
  enabled: true
  api_port: 8080

rules:
  initial_positions:
    type: "file"
    source: "./data/initial_sfens/start_sfens_ply24.txt"
  repetition_occurrences_to_draw: 2

spsa:
  space: "./configs/resources/spsa/rshogi-az-mcts.yaml"  # space spec へのパス（必須）
  num_updates: 50          # 総更新ステップ数（必須）
  pairs_per_update: 2      # 1 更新あたりの対局ペア数
  inflight_factor: 4       # 先行投入する更新バッチ数
  num_parallel: 4          # 並列ワーカー数
  algorithm:
    name: classic
    alpha: 0.602
    gamma: 0.101
    A:
      mode: ratio          # ratio | absolute
      value: 0.1
  variants:
    pairing: plus_minus
    crn: true
    integer_rounding: stochastic
    apply:
      clear_hash: true
      after_setoption: isready
```

完全な例は `configs/run/spsa/example.yaml` を参照してください。

### エンジン設定

SPSA モードでは `engines` リストに**1つのエンジンのみ**を指定します。システム内部で「ベースライン（変更前）」と
「Tuned（摂動後）」の plus/minus バリアントに複製され、対戦が行われます。

### 開始局面

開始局面は `rules.initial_positions` で指定します（`type: file` のみ対応）。SPSA は手番を入れ替えたペア対局を
前提とするため、`flip_policy` を指定する場合は `pair_both` のみ許容されます。

### SPSA パラメータ (`spsa`)

| フィールド | 既定値 | 説明 |
| --- | --- | --- |
| `space` | （必須） | SPSA space spec YAML へのパス。チューニング対象パラメータを定義します。 |
| `num_updates` | （必須） | 総更新ステップ数。 |
| `pairs_per_update` | 1 | 1 更新あたりの対局ペア数（勾配推定に使うバッチサイズ）。 |
| `inflight_factor` | 4 | 先行して投入する更新バッチ数。スループット向上のため複数更新分の対局を並行投入します。 |
| `num_parallel` | 4 | 並列ワーカー数。 |
| `algorithm` | classic | 減衰係数ブロック（下記）。 |
| `variants` | — | バリアント生成・適用ポリシー（下記）。 |
| `snap_float_to_step` | false | 浮動小数点パラメータを step 単位に丸めるか。 |
| `int_ck_floor` | 0.5 | 整数パラメータの摂動 `c_k` の下限。 |
| `early_stop` | なし | 早期終了条件。 |
| `ltc_regression` | なし | LTC 回帰テスト（下記）。 |

### アルゴリズムブロック (`spsa.algorithm`)

| フィールド | 既定値 | 説明 |
| --- | --- | --- |
| `name` | classic | アルゴリズム名。 |
| `alpha` | 0.602 | ステップサイズ `a_k` の減衰指数。 |
| `gamma` | 0.101 | 摂動幅 `c_k` の減衰指数。 |
| `A` | — | 安定化項。`{mode: ratio\|absolute, value: N}` で指定。`ratio` は `num_updates` に対する比率、`absolute` は実数。数値を直接書くと `absolute` 扱い。 |

### バリアントブロック (`spsa.variants`)

| フィールド | 既定値 | 説明 |
| --- | --- | --- |
| `pairing` | plus_minus | バリアントペアリング方式。 |
| `crn` | true | Common Random Numbers（共通乱数）による分散削減を有効にするか。 |
| `integer_rounding` | stochastic | 整数パラメータの丸め方式（`stochastic` 等）。 |
| `instance_affinity` | update | バリアントとインスタンスの割り当て方針。 |
| `apply` | — | バリアント切替時の適用挙動。`clear_hash`（ハッシュクリア）、`after_setoption`（`setoption` 後の同期、例: `isready`）。 |

### LTC Regression (`spsa.ltc_regression`)

チューニング中に定期的に検証対局（LTC: Long Time Control）を行い、性能低下を防ぐ機能です。

- `every_n_updates`: 何ステップごとに検証するか。
- `total_pairs`: 検証対局数。
- `time_control`: LTC 用の持ち時間。
- `pass_criteria`: 通過条件（`max_elo_drop` など）。

## SPSA space protocol

チューニング対象パラメータは `schema_version: shogiarena.spsa.space.v1` の **space spec** ファイルに定義します。
これによりエンジンの USI オプション空間と SPSA ループが疎結合になり、エンジンごとのチューニング対象を
独立したファイルとして管理できます。

```yaml
schema_version: shogiarena.spsa.space.v1

target:
  engine_family: rshogi-az
  protocol: usi_options          # USI setoption 経由でパラメータを適用
  required_options_policy: strict
  tunable_manifest:
    required: false
    command: usi_tunables

parameters:
  - id: cpuct
    label: Cpuct
    target:
      option: Tune.Cpuct           # 実際に setoption するオプション名
      value_encoding: decimal
    value_type: float
    initial: 1.745
    bounds:
      min: 0.5
      max: 4.0
    schedule:
      c_end: 0.05                  # 最終ステップでの摂動幅
      r_end: 0.002                 # 最終ステップでの学習率
    significant_digits: 6

  - id: max_collision_visits
    label: Max collision visits
    target:
      option: Tune.MaxCollisionVisits
      value_encoding: integer
    value_type: int
    initial: 8
    bounds:
      min: 1
      max: 64
    schedule:
      c_end: 2
      r_end: 0.002
    rounding:
      mode: stochastic
```

| フィールド | 説明 |
| --- | --- |
| `target.engine_family` | 対象エンジンファミリ識別子。 |
| `target.protocol` | パラメータ適用プロトコル（`usi_options`）。 |
| `parameters[].id` | パラメータ識別子（成果物・ダッシュボードでのキー）。 |
| `parameters[].target.option` | エンジンへ `setoption` する USI オプション名。 |
| `parameters[].value_type` | `float` または `int`。 |
| `parameters[].initial` | 初期値。 |
| `parameters[].bounds` | 探索範囲 `{min, max}`。 |
| `parameters[].schedule` | ゲインスケジュール（`c_end`: 最終摂動幅、`r_end`: 最終学習率）。 |
| `parameters[].rounding` | 整数パラメータの丸め方式（`{mode: stochastic}` 等）。 |

サンプルは `configs/resources/spsa/` を参照してください。

## 実行結果

SPSA の実行結果は `output_dir/spsa/runs/<config_stem または experiment_name>-<hash8>/YYYYMMDDHHMMSS/`
に保存されます。run ディレクトリ直下には共通の run artifact（`game.db`, `state.json`, `manifest.json` など）が、
`spsa/` サブディレクトリに SPSA 固有の成果物が出力されます。

- **spsa/index.json**: 各更新ステップのパラメータ・勾配・ステップ幅などのスナップショット。
- **spsa/events.jsonl**: SPSA ループのイベントストリーム（ダッシュボードのライブ更新に使用）。
- **spsa/results.jsonl**: 更新ごとの確定結果レコード。
- **dashboard**: SPSA の収束状況を確認できます（`http://localhost:8080`）。
