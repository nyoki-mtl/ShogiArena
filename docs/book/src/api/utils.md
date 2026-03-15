# Utils API

> [!WARNING]
> このページは contributor 向けの内部リファレンスです。ここで扱う helper は `_core.shared.kernel` と `_core.platform` の内部部品であり、安定した public facade ではありません。

ShogiArena の横断 helper は、昔の `utils/` という見え方ではなく、現在は主に `_core.shared.kernel` と `_core.platform` に分かれています。

## 主な分類

- game result / color helper
- path placeholder 解決
- CPU / host probe
- process-wide settings / project dirs

## game result helper

```python
from shogiarena._core.shared.kernel.game_record_types import (
    Color,
    game_result_score,
)
from shogiarena._core.shared.kernel.game_results import (
    GameResult,
    STARTING_SFEN,
    game_result_name,
    game_result_terminal_kind,
    timeout_win_result,
)
```

### 主なもの

- `Color`
  `rshogi.types.Color`
- `GameResult`
  `rshogi.record.GameResult`
- `game_result_score(result, perspective)`
  指定 side 視点の score を返します
- `timeout_win_result(winner)`
  時間切れ勝ちの `GameResult` を返します
- `STARTING_SFEN`
  標準初期局面
- `game_result_terminal_kind(result)`
  `GameRecord` 末尾で使う terminal kind 文字列へ変換します

## path helper

```python
from shogiarena._core.shared.kernel.paths import (
    PATH_OPTION_KEYS,
    maybe_resolve_path_option,
    resolve_path_like,
)
```

### `resolve_path_like()`

`{output_dir}` / `{engine_dir}` の placeholder と環境変数、`~` 展開をまとめて処理します。

```python
resolved = resolve_path_like(
    "{output_dir}/runs/example/game.db",
)
```

### `maybe_resolve_path_option()`

USI option 名が path 系なら解決し、そうでなければ値をそのまま返します。

### `PATH_OPTION_KEYS`

path 扱いする USI option 名の集合です。現在は次を含みます。

- `EvalDir`
- `BookDir`
- `Book_File`
- `DNN_Model`

## CPU / host probe

```python
from shogiarena._core.platform.host_probe.cpu_detection import detect_target_cpu
from shogiarena._core.platform.host_probe.platform_detectors import get_cpu_info
```

- `get_cpu_info()`
  OS ごとの probe をまとめた CPU 情報取得
- `detect_target_cpu()`
  probe 結果を `TARGET_CPU` 文字列へマップ

## settings / project dirs

```python
from shogiarena._core.platform.settings.facade import (
    configure_settings,
    current_settings,
    load_settings,
)
from shogiarena._core.platform.settings import project_dirs
```

### `load_settings()` / `configure_settings()` / `current_settings()`

process-wide settings を読み込み、必要なら再設定します。

### `project_dirs`

module-level に次の runtime path を保持します。

- `output_dir`
- `engine_dir`
- `repos`
- `overlays`
- `settings_path`
- `log_root_dir`

settings 再読込時は `project_dirs._apply_settings()` が内部で更新されます。

## 使用例

```python
from shogiarena._core.platform.host_probe.cpu_detection import detect_target_cpu
from shogiarena._core.shared.kernel.game_record_types import Color, game_result_score
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.paths import resolve_path_like

score = game_result_score(GameResult.BLACK_WIN, Color.BLACK)
engine_log = resolve_path_like("{output_dir}/logs/engine.log")
target_cpu = detect_target_cpu()
```

## 設計メモ

- helper は `utils` という単独 package よりも、責務ごとの `_core.shared.kernel` / `_core.platform` に寄せています
- public API としてはまだ切り出していないので、将来的に配置や命名が変わる余地があります
