# Core API

基本的なデータ構造とコアクラスのリファレンスです。

## ArenaConfig

トーナメント全体の設定を保持するクラスです。

```python
from shogiarena.arena.configs.tournament import ArenaConfig

config = ArenaConfig.from_yaml("configs/arena/my_tournament.yaml")
```

### 主要フィールド

| フィールド | 型 | 説明 |
|----------|-----|------|
| `experiment_name` | str | トーナメント名 |
| `engines` | list[EngineSpec] | 参加エンジンのリスト |
| `tournament` | TournamentConfig | トーナメント方式の設定 |
| `rules` | RulesConfig | ルール設定 |
| `rating` | RatingConfig | レーティング設定 |
| `dashboard` | DashboardConfig | ダッシュボード設定 |
| `work_dir` | Path | 作業ディレクトリ |
| `run_dir` | Path | 実行ディレクトリ |

### メソッド

#### `from_yaml(path: str | Path) -> ArenaConfig`

YAML ファイルから設定を読み込みます。プレースホルダや環境変数も自動的に解決されます。

```python
config = ArenaConfig.from_yaml("configs/arena/tournament.yaml")
```

## EngineSpec

エンジンの仕様を定義するクラスです。

### 主要フィールド

| フィールド | 型 | 説明 |
|----------|-----|------|
| `name` | str | エンジン表示名 |
| `engine_config` | Path \| None | エンジン設定ファイルのパス |
| `artifact` | str \| None | アーティファクト指定（例: `YaneuraOu/<commit>`） |
| `build_options` | dict | ビルドオプション（`target_cpu` など） |
| `options` | dict | USI オプションの上書き |
| `time_control` | TimeControlLimits \| None | このエンジン固有の持ち時間 |
| `cpu_affinity` | tuple[int, ...] | CPU アフィニティ |

## TimeControlLimits

持ち時間の制限を定義するクラスです。

### フィールド

| フィールド | 型 | 説明 |
|----------|-----|------|
| `time_ms` | int \| None | 基本持ち時間（ミリ秒） |
| `increment_ms` | int \| None | フィッシャーインクリメント |
| `byoyomi_ms` | int \| None | 秒読み |
| `fixed_time_ms` | int \| None | 1手あたり固定時間 |
| `depth_limit` | int \| None | 深さ制限 |
| `node_limit` | int \| None | ノード数制限 |

## GameInfo

対局結果と棋譜情報を保持するクラスです。

### 主要フィールド

| フィールド | 型 | 説明 |
|----------|-----|------|
| `game_name` | str | 対局名 |
| `black_player_name` | str | 先手名 |
| `white_player_name` | str | 後手名 |
| `game_result` | GameResult | 結果 (Enum) |
| `init_position_sfen` | str | 初期局面 SFEN |
| `moves` | list[int] | 指し手リスト (cshogi 形式) |
| `eval_values` | list[int \| None] | 評価値履歴 |
| `move_times_ms` | list[int \| None] | 思考時間履歴 |
| `start_date` | datetime | 開始時刻 |

### メソッド

#### `export_to_kif() -> str`
KIF 形式の文字列を返します。

#### `export_to_csa() -> str`
CSA 形式の文字列を返します。

## RulesConfig

対局ルールの設定を保持するクラスです。

### フィールド

| フィールド | 型 | 説明 |
|----------|-----|------|
| `time_control` | TimeControlLimits | 共通の持ち時間設定 |
| `initial_positions` | InitialPositions | 初期局面設定 |
| `adjudication` | AdjudicationSettings | 判定設定 |
| `repetition_occurrences_to_draw` | int | 千日手判定の回数 (2, 3, 4) |

## InitialPositions

初期局面の設定を保持するクラスです。

```python
from shogiarena.arena.configs.base import InitialPositions

# 平手から開始
positions = InitialPositions(type="startpos")

# ファイルから読み込み
positions = InitialPositions(
    type="file",
    source="openings/standard.sfen",
    flip_policy="pair_both"
)
```

## AdjudicationSettings

投了・最大手数判定の設定を保持するクラスです。

### フィールド

| フィールド | 型 | 説明 |
|----------|-----|------|
| `enable_resign` | bool | 投了判定を有効化 |
| `resign_score_cp` | int | 投了スコア閾値 |
| `resign_move_count` | int | 連続何手で投了か |
| `enable_max_plies` | bool | 最大手数による引き分けを有効化 |
| `max_plies` | int | 最大手数 |