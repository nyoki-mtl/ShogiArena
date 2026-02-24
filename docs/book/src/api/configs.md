# Configs API

トーナメント・SPSA 実行に使用する設定モデルの API リファレンスです。

```python
from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.configs.spsa import SpsaRunConfig
```

---

## トーナメント設定

---

## TournamentRunConfig クラス

トーナメント全体の構成を保持するメインの設定クラスです。

```python
from shogiarena.arena.configs.tournament import TournamentRunConfig

config = TournamentRunConfig.from_yaml("configs/run/arena/tournament.yaml")
```

#### from_yaml()

YAML ファイルから設定を読み込みます。

```python
config = TournamentRunConfig.from_yaml(path)
```

- **path**: `str | Path` YAML ファイルのパス
- **戻り値**: `TournamentRunConfig` 設定インスタンス

---

#### from_mapping()

辞書から設定を構築します。

```python
config = TournamentRunConfig.from_mapping(data, base_dir=None)
```

- **data**: `dict` 設定データの辞書
- **base_dir**: `Path | None` (デフォルト: `None`) 相対パス解決の基準ディレクトリ
- **戻り値**: `TournamentRunConfig` 設定インスタンス

---

#### 主要フィールド

- **experiment_name**: `str` 実験名（未指定時はファイル名から自動生成）
- **engines**: `list[EngineConfig]` 参加エンジンのリスト（2 台以上必須）
- **tournament**: `TournamentConfig` トーナメント設定
- **rules**: `RulesConfig` 対局ルール
- **sprt**: `SprtConfig | None` SPRT 設定（2 エンジン時のみ）
- **openbench**: `OpenBenchConfig | None` OpenBench/ShogiBench 提出設定（`enabled=true` には `sprt` 必須）
- **rating**: `RatingConfig` レーティング設定
- **dashboard**: `DashboardConfig` ダッシュボード設定
- **records_output**: `RecordOutputConfig | None` 教師局面バイナリ出力設定
- **instances**: `tuple[Path, ...]` インスタンス定義ファイルのパス
- **output_dir**: `Path` 出力ディレクトリ

---

## EngineConfig クラス

エンジンの起動パラメータを定義します。

```python
from shogiarena.arena.configs.tournament import EngineConfig
```

- **engine_path**: `Path | None` エンジン設定ファイルのパス
- **artifact**: `str | None` アーティファクト指定（`repo/commit` 形式）
- **build_options**: `dict[str, Any]` ビルドオプション
- **options**: `dict[str, Any]` USI オプションの上書き
- **options_overlays**: `list[Path]` オプションオーバーレイファイル
- **mate_default_ply_limit**: `int | None` `think_mate` 既定の手数制限
- **mate_default_node_limit**: `int | None` `think_mate` 既定のノード制限
- **mate_default_infinite**: `bool` `think_mate` 既定を `go mate infinite` にする
- **mate_wait_for_bestmove**: `bool` `checkmate` 後に `bestmove` 受信まで待つ既定値
- **isready_sync_strategy**: `"direct" | "wait" | "stop"` `trigger_isready` 時の同期戦略
- **handshake_timeout**: `float | None` エンジンハンドシェイクのタイムアウト（秒）
- **time_control**: `TimeControlLimits | None` エンジン固有の持ち時間
- **instance_id**: `str | None` インスタンス ID の割り当て
- **cpu_affinity**: `tuple[int, ...] | None` CPU アフィニティ
- **name**: `str` エンジン名（未指定時は自動生成）
- **name_style**: `"hash" | "short"` 自動名のサフィックス長

> **Note: engine_path と artifact は排他**
>
> `engine_path` と `artifact` は同時に指定できません。

---

## TournamentConfig クラス

トーナメントの実行方法を定義します。

```python
from shogiarena.arena.configs.tournament import TournamentConfig
```

- **scheduler**: `str` (デフォルト: `"round_robin"`) スケジューラ種別
- **games_per_pair**: `int` (デフォルト: `4`) ペアあたりの対局数
- **num_parallel**: `int` (デフォルト: `4`) 並列対局数
- **seed**: `int` (デフォルト: `42`) 乱数シード
- **game_order**: `Literal["auto", "pairwise", "interleave", "shuffle"]` (デフォルト: `"auto"`) 対局順序
- **baseline_count**: `int` (デフォルト: `1`) ベースラインエンジンの数（gauntlet スケジューラ用）

---

## RatingConfig クラス

レーティング計算の設定です。

```python
from shogiarena.arena.configs.tournament import RatingConfig
```

- **initial**: `float` (デフォルト: `1500.0`) 初期レーティング
- **k_factor**: `float` (デフォルト: `16.0`) K ファクター

---

## DashboardConfig クラス

ダッシュボードの設定です。

```python
from shogiarena.arena.configs.tournament import DashboardConfig
```

- **enabled**: `bool` (デフォルト: `True`) ダッシュボードを有効化
- **api_port**: `int` (デフォルト: `8080`) API ポート

---

## RecordOutputConfig クラス

自己対局の棋譜を psv/sbinpack 形式でバイナリ保存するための設定です。

```python
from shogiarena.arena.configs.tournament import RecordOutputConfig
```

- **format**: `"psv" | "sbinpack"` 出力フォーマット
- **max_positions_per_file**: `int` (デフォルト: `1_000_000`) 1 ファイルあたりの局面数上限
- **max_games_per_file**: `int | None` (デフォルト: `None`) 1 ファイルあたりの対局数上限（sbinpack のみ）
- **output_dir**: `Path | None` (デフォルト: `None`) 出力先ディレクトリ
- **file_prefix**: `str | None` (デフォルト: `None`) 出力ファイルの接頭辞

> **Note: 対局境界でのみローテーション**
>
> sbinpack は対局の途中でファイルを分割しません。`max_games_per_file` を指定した場合は対局数で固定し、未指定の場合は `max_positions_per_file` を超過した時点の対局まで書き込んでから次ファイルへ切り替えます。psv は 1 局面 = 1 レコードのため `max_games_per_file` は使用できません。

---

## 共通ルール設定

---

## RulesConfig クラス

対局ルールの共通設定です。

```python
from shogiarena.arena.configs.base import RulesConfig
```

- **time_control**: `TimeControlLimits | None` 持ち時間設定
- **initial_positions**: `InitialPositionConfig` 初期局面設定
- **adjudication**: `AdjudicationSettings` 判定設定
- **repetition_occurrences_to_draw**: `int` 千日手判定回数（2/3/4）

---

## TimeControlLimits クラス

持ち時間の設定です。

```python
from shogiarena.arena.engines.time_control import TimeControlLimits
```

- **time_ms**: `int | None` 基本持ち時間（ミリ秒）
- **increment_ms**: `int | None` フィッシャーインクリメント
- **byoyomi_ms**: `int | None` 秒読み
- **fixed_time_ms**: `int | None` 1 手あたり固定時間
- **depth_limit**: `int | None` 探索深さ制限
- **node_limit**: `int | None` ノード数制限

#### to_spec_str()

短縮文字列に変換します。

```python
spec = tc.to_spec_str()  # 例: "t30000+b1000"
```

- **戻り値**: `str` 短縮文字列表現

---

#### from_spec_str()

短縮文字列からパースするクラスメソッドです。

```python
tc = TimeControlLimits.from_spec_str("t30000+b1000")
```

- **s**: `str` 短縮文字列
- **戻り値**: `TimeControlLimits` パースされた設定

---

## InitialPositionConfig クラス

初期局面の生成方法を定義します。

```python
from shogiarena.arena.configs.base import InitialPositionConfig
```

- **type**: `"startpos" | "file"` (デフォルト: `"startpos"`) 局面タイプ
- **source**: `str | None` (デフォルト: `None`) SFEN ファイルパス（`type="file"` 時）
- **flip_policy**: `str` (デフォルト: `"pair_both"`) 先後入れ替えポリシー

#### flip_policy の値

- **`"alternate"`** -- 交互に先後を入れ替え
- **`"random"`** -- ランダムに決定
- **`"none"`** -- 入れ替えなし
- **`"pair_both"`** -- 同一局面で両方向対局（SPSA 用）

---

## AdjudicationSettings クラス

投了・最大手数などの判定設定です。`configs/base.py` に定義されています。

> **Note**: サービス層の `AdjudicationConfig`（`services/game_control`）とは別のクラスです。

```python
from shogiarena.arena.configs.base import AdjudicationSettings

settings = AdjudicationSettings(
    resign_threshold_cp=800,
    resign_move_count=8,
    resign_two_sided=True,
    enable_max_plies=True,
    max_plies=320,
)
```

- **resign_threshold_cp**: `int | None` (デフォルト: `None`) 投了閾値（センチポーン）。`None` で無効
- **resign_move_count**: `int` (デフォルト: `8`) 連続手数
- **resign_two_sided**: `bool` (デフォルト: `True`) 両側に投了チェックを適用
- **enable_max_plies**: `bool` (デフォルト: `True`) 最大手数判定を有効化
- **max_plies**: `int | None` (デフォルト: `320`) 最大手数
- **sync_max_plies_with_engine**: `bool` (デフォルト: `True`) エンジン側の最大手数オプションと同期
- **engine_max_ply_option_names**: `str | list[str]` (デフォルト: `"auto"`) エンジン側の最大手数オプション名

---

## SystemConfig クラス

システムレベルの設定です。

```python
from shogiarena.arena.configs.tournament import SystemConfig
```

- **resource_poll_interval**: `float` (デフォルト: `0.1`) インスタンスリソース待機の初期間隔
- **resource_poll_max_interval**: `float` (デフォルト: `1.0`) インスタンスリソース待機の最大間隔

---

## SPRT 設定

---

## SprtConfig クラス

SPRT（Sequential Probability Ratio Test）の設定です。

```python
from shogiarena.arena.configs.base import SprtConfig

sprt = SprtConfig(elo0=0.0, elo1=5.0, alpha=0.05, beta=0.05)
```

- **elo0**: `float` (デフォルト: `0.0`) 帰無仮説の Elo 差
- **elo1**: `float` (デフォルト: `5.0`) 対立仮説の Elo 差
- **alpha**: `float` (デフォルト: `0.05`) 第 1 種の誤り率
- **beta**: `float` (デフォルト: `0.05`) 第 2 種の誤り率
- **min_games**: `int` (デフォルト: `0`) 最小対局数
- **max_games**: `int | None` (デフォルト: `None`) 最大対局数
- **num_parallel**: `int | None` (デフォルト: `None`) 並列度

---

## OpenBenchConfig クラス

OpenBench/ShogiBench への提出設定です。

```python
from shogiarena.arena.configs.base import OpenBenchConfig
```

- **enabled**: `bool` (デフォルト: `False`) 提出機能の有効化
- **mode**: `"existing_test" | "create_test"` (デフォルト: `"existing_test"`) 既存 test へ提出するか、テスト作成から行うか
- **server**: `str | None` (デフォルト: `None`) OpenBench サーバ URL
- **username**: `str | None` (デフォルト: `None`) OpenBench ユーザー名
- **password_env**: `str` (デフォルト: `"OPENBENCH_PASSWORD"`) パスワードを読む環境変数名
- **target_test_id**: `int | None` (デフォルト: `None`) 提出対象の test ID
- **submit_interval_games**: `int` (デフォルト: `2`) 何局ごとに差分を送信するか
- **strict**: `bool` (デフォルト: `True`) 送信失敗時に run を停止するか
- **poll_interval_sec**: `float` (デフォルト: `5.0`) workload 取得のポーリング間隔（秒）
- **assignment_timeout_sec**: `float` (デフォルト: `120.0`) target test 割当待ちタイムアウト（秒）
- **allow_insecure_http**: `bool` (デフォルト: `False`) `http://` サーバを許可するか
- **create**: `OpenBenchCreateConfig | None` (デフォルト: `None`) `mode=create_test` 時の作成設定

> **Note: 有効化時の必須条件**
>
> `enabled=true` の場合、`server` / `username` / `password_env` は必須です。
> `mode=existing_test` では `target_test_id` 必須、`mode=create_test` では `create` 必須です。

---

## OpenBenchCreateConfig クラス

`mode=create_test` 時に利用する設定です。

```python
from shogiarena.arena.configs.base import OpenBenchCreateConfig
```

- **discovery_timeout_sec**: `float` (デフォルト: `180.0`) CREATE_TEST 後に test id を見つける待機時間
- **payload**: `OpenBenchCreatePayload` CREATE_TEST へ送信するフォーム項目

---

## SPSA 設定

---

## SpsaRunConfig クラス

SPSA チューニングの設定です。

```python
from shogiarena.arena.configs.spsa import SpsaRunConfig, load_config_yaml

config = load_config_yaml("configs/run/spsa/tune.yaml")
```

- **parameters_path**: `str` パラメータファイルのパス
- **start_sfens_path**: `str` 初期 SFEN ファイルのパス
- **baseline**: `list[EngineConfig]` ベースラインエンジン（1 台）
- **tuned**: `list[EngineConfig]` チューニング対象エンジン（1 台）
- **rules**: `RulesConfig` 対局ルール
- **num_updates**: `int` 更新回数
- **dashboard**: `DashboardConfig` ダッシュボード設定
- **system**: `SystemConfig` システム設定

#### SPSA パラメータ

- **mobility**: `float` 摂動の大きさ
- **scale**: `float` スケーリング係数
- **a0**: `float` 学習率の初期値
- **A**: `float` 学習率の減衰パラメータ
- **alpha**: `float` 学習率の減衰指数
- **gamma**: `float` 摂動の減衰指数

---

## LtcRegressionConfig クラス

LTC（Long Time Control）回帰テストの設定です。

```python
from shogiarena.arena.configs.spsa import LtcRegressionConfig
```

- **enabled**: `bool` 有効化フラグ
- **every_n_updates**: `int` 何更新ごとにテストするか
- **total_pairs**: `int` テスト対局数
- **time_control**: `TimeControlLimits` テスト用持ち時間
- **pass_criteria**: `LtcPassCriteria` 合格条件

---

## 使用例

### YAML から設定を読み込む

```python
from shogiarena.arena.configs.tournament import TournamentRunConfig

config = TournamentRunConfig.from_yaml("configs/run/arena/tournament.yaml")
print(f"エンジン数: {len(config.engines)}")
print(f"対局数/ペア: {config.tournament.games_per_pair}")
```

### プログラムで設定を構築する

```python
from pathlib import Path

from shogiarena.arena.configs.tournament import TournamentRunConfig, EngineConfig, TournamentConfig
from shogiarena.arena.configs.base import RulesConfig
from shogiarena.arena.engines.time_control import TimeControlLimits

config = TournamentRunConfig(
    experiment_name="test_tournament",
    engines=[
        EngineConfig(engine_path=Path("configs/engine/engine1.yaml"), name="Engine1"),
        EngineConfig(engine_path=Path("configs/engine/engine2.yaml"), name="Engine2"),
    ],
    tournament=TournamentConfig(games_per_pair=10, num_parallel=4),
    rules=RulesConfig(
        time_control=TimeControlLimits(byoyomi_ms=1000),
    ),
    output_dir=Path("output"),
)
```

---

## 関連ドキュメント

- [設定ファイルガイド](../user-guide/configuration.md) - YAML 設定の書き方
- [エンジン設定](../user-guide/engine-configuration.md) - エンジン設定の詳細
