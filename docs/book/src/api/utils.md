# Utils API

ユーティリティ関数・型定義のリファレンスです。

## 型定義

### Color

```python
from shogiarena.utils.types.types import Color
```

手番を表す列挙型です。`rshogi.types.Color` を再エクスポートしています。

**使用例:**

```python
color = Color.BLACK
opposite = color.flip()  # Color.WHITE
```

#### 値

- **`Color.BLACK`**: 先手
- **`Color.WHITE`**: 後手

#### メソッド

#### `flip()`

```python
color.flip() -> Color
```

反転した手番を返します。

---

#### `is_black()`

```python
color.is_black() -> bool
```

先手か判定します。

---

#### `is_white()`

```python
color.is_white() -> bool
```

後手か判定します。

---

### GameResult

```python
from shogiarena.utils.types.types import GameResult
```

対局結果を表す列挙型です。`rshogi.record.GameResult` を再エクスポートしています。

**使用例:**

```python
result = GameResult.BLACK_WIN
print(result.is_black_win())  # True
```

#### 主な値

- **`BLACK_WIN`**: 先手勝ち
- **`WHITE_WIN`**: 後手勝ち
- **`DRAW_BY_REPETITION`**: 千日手
- **`DRAW_BY_MAX_PLIES`**: 最大手数/持将棋
- **`BLACK_WIN_BY_DECLARATION`**: 先手宣言勝ち
- **`WHITE_WIN_BY_DECLARATION`**: 後手宣言勝ち
- **`BLACK_WIN_BY_TIMEOUT`**: 先手時間切れ勝ち
- **`WHITE_WIN_BY_TIMEOUT`**: 後手時間切れ勝ち

#### 主なメソッド

- `is_black_win()` - 先手勝ちか判定
- `is_white_win()` - 後手勝ちか判定
- `is_draw()` - 引き分けか判定
- `is_win()` - 勝敗が決したか判定
- `is_win_by_declaration()` - 宣言勝ちか判定

---

### ShogiArena 補助関数

`GameResult` と `Color` に関する補助関数です。

#### `timeout_win_result()`

```python
shogiarena.utils.types.types.timeout_win_result(winner: Color) -> GameResult
```

勝者色から時間切れ勝ち結果を生成します。

**引数:**
- **winner**: `Color` 勝者の手番

**戻り値:**
- `GameResult`: 時間切れ勝ちの結果（`BLACK_WIN_BY_TIMEOUT` または `WHITE_WIN_BY_TIMEOUT`）

**使用例:**

```python
from shogiarena.utils.types.types import Color, timeout_win_result

result = timeout_win_result(Color.WHITE)
print(result)  # GameResult.WHITE_WIN_BY_TIMEOUT
```

---

#### `game_result_score()`

```python
shogiarena.utils.types.types.game_result_score(
    result: GameResult,
    perspective: Color,
) -> float | None
```

指定した手番の視点でのスコアを返します。

**引数:**
- **result**: `GameResult` 対局結果
- **perspective**: `Color` 視点の手番

**戻り値:**
- `float | None`: 勝ち=`1.0`、引き分け=`0.5`、負け=`0.0`。有効な結果でない場合は `None`

**使用例:**

```python
from shogiarena.utils.types.types import Color, GameResult, game_result_score

result = GameResult.BLACK_WIN
black_score = game_result_score(result, Color.BLACK)
print(black_score)  # 1.0
```

---

## パス解決

```python
from shogiarena.utils.common.paths import resolve_path_like, expand_env_vars
```

### `resolve_path_like()`

```python
shogiarena.utils.common.paths.resolve_path_like(
    s: str,
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
    extra_placeholders: dict[str, str] | None = None,
) -> str
```

プレースホルダと環境変数を展開してパスを解決します。

**引数:**
- **s**: `str` 展開する文字列
- **output_dir**: `Path | None` (デフォルト: `None`) 出力ディレクトリ
- **engine_dir**: `Path | None` (デフォルト: `None`) エンジンディレクトリ
- **extra_placeholders**: `dict[str, str] | None` (デフォルト: `None`) 追加のプレースホルダ

**戻り値:**
- `str`: 展開されたパス文字列

#### サポートするプレースホルダ

- `{output_dir}` - 出力ディレクトリ
- `{engine_dir}` - エンジンディレクトリ

**使用例:**

```python
from shogiarena.utils.common.paths import resolve_path_like

# プレースホルダを展開
db_path = resolve_path_like("{output_dir}/runs/test/game.db")

# カスタムプレースホルダを追加
custom_path = resolve_path_like(
    "{run_dir}/logs",
    extra_placeholders={"{run_dir}": "/tmp/run_001"}
)
```

---

### `expand_env_vars()`

```python
shogiarena.utils.common.paths.expand_env_vars(s: str) -> str
```

環境変数を展開します。POSIX（`$VAR`, `${VAR}`）と Windows（`%VAR%`）形式をサポートします。

**使用例:**

```python
from shogiarena.utils.common.paths import expand_env_vars

path = expand_env_vars("$HOME/shogiarena")
```

---

### `is_path_option_key()`

```python
shogiarena.utils.common.paths.is_path_option_key(name: str) -> bool
```

USI オプションキーがパスとして扱うべきか判定します。

---

### PATH_OPTION_KEYS

```python
from shogiarena.utils.common.paths import PATH_OPTION_KEYS
```

パスとして扱うべき USI オプションキーの定数セットです。

- `EvalDir`
- `BookDir`
- `Book_File`
- `DNN_Model`

## プロジェクトディレクトリ

```python
from shogiarena.utils.common.project_dirs import project_dirs
```

ShogiArena のプロジェクトディレクトリを管理するシングルトンです。

#### 属性

- **`project_dirs.output_dir`**: 出力ディレクトリ
- **`project_dirs.engine_dir`**: エンジンキャッシュディレクトリ
- **`project_dirs.repos`**: リポジトリキャッシュ
- **`project_dirs.overlays`**: オーバーレイディレクトリ
- **`project_dirs.settings_path`**: settings.yaml のパス
- **`project_dirs.log_root_dir`**: ログルートディレクトリ

## CPU 情報

```python
from shogiarena.utils.cpuinfo import get_cpu_info, detect_target_cpu
```

### `get_cpu_info()`

```python
shogiarena.utils.cpuinfo.get_cpu_info() -> dict
```

CPU 情報を取得します。

**戻り値:**
- `dict`: CPU 情報を含む辞書

**使用例:**

```python
from shogiarena.utils.cpuinfo import get_cpu_info

info = get_cpu_info()
print(info["arch"])       # "X86_64"
print(info["brand_raw"])  # "Intel(R) Core(TM) i7-..."
print(info["flags"])      # ["avx", "avx2", "sse4_2", ...]
```

#### 戻り値のキー

- **`arch`**: アーキテクチャ（`X86_64`, `ARM_8` など）
- **`arch_string_raw`**: 生のアーキテクチャ文字列
- **`vendor_id_raw`**: ベンダー ID
- **`brand_raw`**: CPU ブランド名
- **`family`**: CPU ファミリー
- **`model`**: CPU モデル
- **`flags`**: CPU フラグのリスト

---

### `detect_target_cpu()`

```python
shogiarena.utils.cpuinfo.detect_target_cpu() -> str
```

YaneuraOu の `TARGET_CPU` 文字列を自動検出します。

**戻り値:**
- `str`: CPU ターゲット文字列（`"AVX2"`, `"AVX512"`, `"SSE42"` など）

**使用例:**

```python
from shogiarena.utils.cpuinfo import detect_target_cpu

target = detect_target_cpu()
print(target)  # "AVX2"
```
