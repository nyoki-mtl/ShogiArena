# Configs API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。掲載されている deep import や internal model は正式な公開 API ではありません。利用者向けの入口は `shogiarena.tournament.load_tournament_config()` を基準にしてください。

ShogiArena の run config は、現在は「モデルを直接 public export する」のではなく、runtime を通して構築する方針です。

## 利用者向けの正規入口

```python
from shogiarena.tournament import load_tournament_config


config = load_tournament_config("tournament.yaml")
```

または mapping から:

```python
from shogiarena.tournament import load_tournament_config


config = load_tournament_config(
    {
        "experiment_name": "example",
        "engines": [
            {"engine_path": "engine1.yaml"},
            {"engine_path": "engine2.yaml"},
        ],
        "tournament": {"games_per_pair": 10, "num_parallel": 2},
        "rules": {"time_control": {"time_ms": 10_000, "increment_ms": 100}},
    }
)
```

`TournamentRunConfig.from_yaml()` のような public loader は用意していません。設定ファイルから読むときは `load_tournament_config()` を使う前提です。

## 内部モデルの正本

実装上の正本は次です。

- tournament config: `shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament.TournamentRunConfig`
- engine config: `shogiarena._core.contexts.game_session.adapters.orchestration.config_engine.EngineConfig`
- runtime build request: `shogiarena._core.contexts.tournament.ports.tournament_runtime_port.TournamentRunConfigBuildRequest`

これらは internal model であり、外部利用者向けの安定 import ではありません。

## Tournament payload の主要セクション

### `experiment_name`

- 実験名
- 未指定時は source path から補完される

### `engines`

- 参加エンジンの配列
- 通常の tournament では 2 台以上が必要
- 各要素は `engine_path` または `artifact` のどちらか一方を持つ

代表フィールド:

- `engine_path`
- `artifact`
- `build_options`
- `options`
- `options_overlays`
- `instance_id`
- `cpu_affinity`
- `time_control`

### `tournament`

- スケジュールと並列実行の基本設定

代表フィールド:

- `scheduler`
- `games_per_pair`
- `num_parallel`
- `seed`
- `game_order`
- `baseline_count`

### `rules`

- 持ち時間、初期局面、判定ルール

代表フィールド:

- `time_control`
- `initial_positions`
- `adjudication`
- `repetition_occurrences_to_draw`

### `sprt`

- 逐次確率比検定の設定
- tournament runner 側で解釈される

代表フィールド:

- `elo0`
- `elo1`
- `alpha`
- `beta`
- `max_games`
- `num_parallel`

### `dashboard`

- dashboard 有効化や API port の設定

### `records_output`

- 教師局面や棋譜のバイナリ出力設定

### `instances`

- instance 定義ファイルの配列
- relative path は build request の `base_dir` を基準に解決される

## path 解決

`load_tournament_config()` は config source に応じて `base_dir` と `source_path` を runtime に渡します。

- file path 入力:
  - `source_path` はそのファイル
  - `base_dir` はその親ディレクトリ
- mapping 入力:
  - `base_dir` を明示しなければ `Path.cwd()`
  - `source_path` を与えた場合はその親を基準にできる

これにより `instances` や `records_output.output_dir`、overlay path の相対解決を一貫させています。

## contributor 向けメモ

- boundary parse は `_core.interfaces.cli.config_file_loaders` が担う
- 実 config 構築は `_core.contexts.tournament.adapters.runtime_adapter.TournamentRuntimeAdapter.build_run_config()` が担う
- public facade は `shogiarena.tournament.load_tournament_config()` に閉じる
