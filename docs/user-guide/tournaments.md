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
| `--overwrite` | 既存の実行ディレクトリ（`run_dir`）を上書きします。**注意: 過去のログやDBは消えます。** |
| `--engine KEY=VALUE ...` | エンジンをCLIで定義します（複数指定可）。`instance_id=...` を含めることで割当も可能です。 |
| `--instances PATH` | インスタンス設定ファイル（YAML）を指定します。デフォルトは `configs/resources/instances/local.yaml` です。 |
| `--provision {none,force}` | SSH リモート実行時にファイルを同期するか制御します。`force` は毎回同期します。 |
| `--git-worktree {strict,clean,allow-dirty}` | ビルド前の Git ワークツリーの状態チェックを制御します。 |

## 設定ファイル (ArenaConfig)

トーナメントの設定は YAML ファイルで記述します。

### 基本構造

```yaml
experiment_name: "my_experiment"  # 実験名（ディレクトリ名などに使用）

engines:
  - name: "EngineA"
    engine_config: "configs/engine/engine_a.yaml"
  - name: "EngineB"
    engine_config: "configs/engine/engine_b.yaml"

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
```

### Engines (`engines`)

参加するエンジンをリストで定義します。

| フィールド | 説明 |
| --- | --- |
| `name` | エンジンの表示名（一意である必要があります）。 |
| `engine_config` | ローカルのエンジン設定 YAML へのパス。 |
| `artifact` | `engine_config` の代わりにビルド済みアーティファクト（例: `YaneuraOu/<commit>`）を指定。 |
| `build_options` | `artifact` 使用時のビルドオプション（`target_cpu` など）。 |
| `options` | USI オプションの上書き設定。 |
| `time_control` | このエンジン固有の持ち時間設定（`rules` より優先されます）。 |
| `cpu_affinity` | CPU コアの固定（例: `"0,2-3"`）。 |

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

#### Adjudication (`adjudication`)

- `enable_resign`: 投了有効化（デフォルト False）。
- `resign_score_cp`: 投了判定スコア（例: 800）。
- `resign_move_count`: 連続何手で投了するか。
- `max_plies`: 最大手数（引き分け判定）。

### Rating (`rating`)

Elo レーティング計算の設定です。

- `initial`: 初期レーティング（デフォルト 1500）。
- `k_factor`: K値（デフォルト 16）。

### Dashboard (`dashboard`)

- `enabled`: ダッシュボードを有効にするか。
- `api_port`: ポート番号。

## ディレクトリ構造

実行結果は `output_dir`（デフォルトは `~/.local/share/shogiarena/output` など）以下に保存されます。

```text
output_dir/
└── tournament/
    └── <config_stem>-<hash8>/
        └── YYYYMMDDHHmmSS/
            ├── game.db         # 結果データベース
            ├── logs/           # 実行ログ
            ├── matches/        # 各対局のログ
            └── artifacts/      # ビルドされたエンジンなど
```
