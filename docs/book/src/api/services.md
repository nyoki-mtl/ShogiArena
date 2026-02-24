# Services API

統計計算・永続化・判定などのサービスの API リファレンスです。

---

## 統計サービス

---

## EloRatingService クラス

Elo レーティング計算サービスです。

```python
from shogiarena.arena.services.statistics.rating_service import EloRatingService

service = EloRatingService(initial_rating=1500.0, k_factor=16.0)
```

- **initial_rating**: `float` (デフォルト: `1500.0`) 新規プレイヤーの初期レーティング
- **k_factor**: `float` (デフォルト: `16.0`) Elo 計算の K ファクター

#### calculate_rating_series()

対局履歴からレーティング推移を計算します。

```python
series = service.calculate_rating_series(games)
```

- **games**: `list[dict[str, Any]]` プレイヤー名と結果を含む対局レコードのリスト
- **戻り値**: `list[dict[str, Any]]` 各エンジンのレーティング推移データ

---

#### get_current_ratings()

全対局を処理した後の現在のレーティングを取得します。

```python
ratings = service.get_current_ratings(games)
```

- **games**: `list[dict[str, Any]]` 対局レコードのリスト
- **戻り値**: `dict[str, float]` エンジン名から現在のレーティングへの辞書

---

#### calculate_rating_difference()

2 エンジン間のレーティング差を算出します。

```python
diff = service.calculate_rating_difference(engine_a, engine_b, games)
```

- **engine_a**: `str` エンジン A の名前
- **engine_b**: `str` エンジン B の名前
- **games**: `list[dict[str, Any]]` 対局レコードのリスト
- **戻り値**: `float` レーティング差 (A - B)

---

#### update_ratings()

内部レーティングキャッシュを対局結果に基づいて更新します。

```python
new_black, new_white = service.update_ratings(
    black_player="Engine1",
    white_player="Engine2",
    game_result=GameResult.BLACK_WIN,
)
```

- **black_player**: `str` 先手プレイヤー名
- **white_player**: `str` 後手プレイヤー名
- **game_result**: `GameResult` 対局結果
- **戻り値**: `tuple[float, float]` (新しい先手レーティング, 新しい後手レーティング)

---

#### calculate_rating_confidence_intervals()

エンジンごとの Elo 信頼区間を近似計算します。

```python
intervals = service.calculate_rating_confidence_intervals(games, confidence=0.95)
```

- **games**: `list[dict[str, Any]]` 対局レコードのリスト
- **confidence**: `float` (デフォルト: `0.95`) 信頼水準 (0 < confidence < 1)
- **戻り値**: `dict[str, tuple[float, float]]` エンジン名から (下限, 上限) への辞書

---

#### get_rating_series_for_engine()

特定エンジンのレーティング履歴を返します。

```python
history = service.get_rating_series_for_engine(games, engine_name="EngineA")
```

- **games**: `list[dict[str, Any]]` 対局レコードのリスト
- **engine_name**: `str` 対象エンジン名
- **戻り値**: `list[tuple[int, float]]` (対局インデックス, レーティング) のリスト

---

## Sprt クラス

逐次確率比検定（SPRT）の実装です。

```python
from shogiarena.arena.services.statistics.sprt import Sprt, SprtDecision, SprtResult

sprt = Sprt(elo0=0, elo1=5, alpha=0.05, beta=0.05)
```

- **elo0**: `float` 帰無仮説の Elo 差（通常 0）
- **elo1**: `float` 対立仮説の Elo 差（例: 5.0）
- **alpha**: `float` (デフォルト: `0.05`) 第 1 種の誤り率（偽陽性）
- **beta**: `float` (デフォルト: `0.05`) 第 2 種の誤り率（偽陰性）

#### add_game_result()

対局結果を追加して SPRT 状態を更新します。

```python
result = sprt.add_game_result(GameResult.BLACK_WIN)
```

- **result**: `GameResult` 対局結果
- **戻り値**: `SprtResult` 現在の SPRT 結果と判定

---

#### get_status()

新しい結果を追加せずに現在の SPRT ステータスを取得します。

```python
status = sprt.get_status()
```

- **戻り値**: `SprtResult` 現在の SPRT 結果

---

#### reset()

新しいテストのために SPRT 状態をリセットします。

```python
sprt.reset()
```

---

#### is_finished()

SPRT テストが判定に到達したかを確認します。

```python
if sprt.is_finished():
    print("テスト完了")
```

- **戻り値**: `bool` 判定が出ていれば `True`

---

#### to_snapshot()

SPRT 状態をシリアライズします（一時停止/再開用）。

```python
snapshot = sprt.to_snapshot()
```

- **戻り値**: `dict[str, float | int]` シリアライズされた SPRT 状態

---

#### from_snapshot()

スナップショットから SPRT 状態を復元するクラスメソッドです。

```python
sprt = Sprt.from_snapshot(snapshot)
```

- **snapshot**: `dict[str, float | int]` シリアライズされた SPRT 状態
- **戻り値**: `Sprt` 復元された SPRT インスタンス

---

## SprtDecision

SPRT テストの判定状態を表す列挙型です。

```python
from shogiarena.arena.services.statistics.sprt import SprtDecision
```

- **`CONTINUE`** -- 継続（判定未確定）
- **`ACCEPT_H0`** -- 帰無仮説を採択（差なし）
- **`ACCEPT_H1`** -- 対立仮説を採択（差あり）

---

## SprtResult クラス

SPRT テスト結果の情報を保持するデータクラスです。

```python
from shogiarena.arena.services.statistics.sprt import SprtResult
```

- **llr**: `float` 対数尤度比
- **lower_bound**: `float` 下限（H0 採択境界）
- **upper_bound**: `float` 上限（H1 採択境界）
- **decision**: `SprtDecision` 判定結果
- **games_played**: `int` 対局数
- **wins**: `int` 勝ち数
- **draws**: `int` 引き分け数
- **losses**: `int` 負け数
- **win_rate**: `float` 勝率
- **elo_estimate**: `float | None` (デフォルト: `None`) 推定 Elo 差

---

## BTDEstimator クラス

Bradley-Terry-Davidson モデルによるレーティング推定です。先手の色アドバンテージと引き分け傾向を考慮します。

```python
from shogiarena.arena.services.statistics.btd import BTDEstimator, BTDEstimate
```

#### estimate()

対局レコードからレーティングを推定します。

```python
estimator = BTDEstimator()
result = estimator.estimate(games, anchor_name=None, engine_names=None)
```

- **games**: `Iterable[dict[str, Any]]` 対局レコード
- **anchor_name**: `str | None` (デフォルト: `None`) 基準エンジン名（R=0 に固定）
- **engine_names**: `Iterable[str] | None` (デフォルト: `None`) 含めるエンジン名の集合
- **戻り値**: `BTDEstimate` 推定結果

---

## BTDEstimate クラス

BTD 推定結果を保持するデータクラスです。

```python
from shogiarena.arena.services.statistics.btd import BTDEstimate
```

- **ratings**: `dict[str, float]` エンジンごとの Elo レーティング
- **rating_se**: `dict[str, float]` エンジンごとの標準誤差（Elo）
- **rating_cov**: `dict[tuple[str, str], float] | None` エンジン間の共分散行列（Elo 空間）
- **anchor**: `str` 基準エンジン名（R=0）
- **gamma_elo**: `float` 先手アドバンテージ（Elo）
- **gamma_elo_se**: `float | None` 先手アドバンテージの標準誤差
- **nu**: `float` 引き分け傾向パラメータ（p_draw(eq) = nu/(1+nu)）
- **nu_se**: `float | None` nu の標準誤差
- **draw_eq**: `float` 同等強度での暗黙の引き分け率
- **draw_eq_se**: `float | None` draw_eq の標準誤差

---

## Pentanomial

ペンタノミアル分布（2 局 1 組の成績分布）を集計する関数です。

```python
from shogiarena.arena.services.statistics.pentanomial import compute_pentanomial

result = compute_pentanomial(db)
```

#### compute_pentanomial()

```python
def compute_pentanomial(db: ArenaDBService) -> dict[str, Any]
```

- **db**: `ArenaDBService` データベースサービスインスタンス
- **戻り値**: `dict[str, Any]` -- `pairs`（ペア数）と `bins`（`"2.0"`, `"1.5"`, `"1.0"`, `"0.5"`, `"0.0"` をキーとする分布）を含む辞書

---

## 永続化サービス

---

## ArenaDBService クラス

SQLite を使った永続化サービスです。コンテキストマネージャとして使用可能です。

```python
from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.db.factory import SQLiteShogiDBFactory

factory = SQLiteShogiDBFactory("game.db")
db_service = ArenaDBService(factory)
```

- **factory**: `BaseFactory` データベースセッション作成用のファクトリ

コンテキストマネージャとしても利用できます:

```python
with ArenaDBService(factory) as db_service:
    counts = db_service.get_game_result_counts()
```

#### ensure_schema_compatibility()

データベーススキーマの存在と利用可能性を確認します。

```python
db_service.ensure_schema_compatibility()
```

---

#### get_shogidb()

内部の `ShogiRepository` インスタンスを公開します。

```python
repo = db_service.get_shogidb()
```

- **戻り値**: `ShogiRepository` ORM リポジトリインスタンス

---

#### get_game_result_counts()

結果コード別の対局数を取得します。

```python
counts = db_service.get_game_result_counts(game_type="arena")
```

- **game_type**: `str` (デフォルト: `"arena"`) クエリ対象のゲームタイプ
- **戻り値**: `dict[GameResult, int]` 結果コードから件数への辞書

---

#### get_games_with_players()

プレイヤー情報付きの対局リストを取得します（レーティング計算向け）。

```python
games = db_service.get_games_with_players(game_type="arena")
```

- **game_type**: `str` (デフォルト: `"arena"`) クエリ対象のゲームタイプ
- **戻り値**: `list[dict[str, Any]]` プレイヤー名と結果を含む対局レコードのリスト

---

#### get_all_games()

ダッシュボード API 向けに全対局を返します。フィルタリングとページネーションをサポートします。

```python
games = db_service.get_all_games(
    game_type="arena",
    offset=0,
    limit=100,
    search_query="Engine1",
    player_names=["EngineA", "EngineB"],
    result_filter=GameResult.BLACK_WIN,
)
```

- **game_type**: `str` (デフォルト: `"arena"`) クエリ対象のゲームタイプ
- **offset**: `int` (デフォルト: `0`) スキップする件数
- **limit**: `int | None` (デフォルト: `None`) 取得する最大件数
- **search_query**: `str | None` (デフォルト: `None`) ゲーム名・プレイヤー名での検索文字列
- **player_names**: `Sequence[str] | None` (デフォルト: `None`) プレイヤー名によるフィルタ
- **result_filter**: `GameResult | None` (デフォルト: `None`) 結果コードによるフィルタ
- **戻り値**: `list[dict[str, Any]]` 対局レコードのリスト

---

#### get_game_id_by_name()

ゲーム名からデータベース ID を取得します。

```python
game_id = db_service.get_game_id_by_name("game_001")
```

- **game_name**: `str` ゲーム名
- **戻り値**: `int | None` データベース ID（見つからない場合は `None`）

---

#### get_color_statistics()

先後別の正確な勝利統計を取得します。

```python
stats = db_service.get_color_statistics(game_type="arena")
# {"black_wins": 120, "white_wins": 105, "draws": 25}
```

- **game_type**: `str` (デフォルト: `"arena"`) クエリ対象のゲームタイプ
- **戻り値**: `dict[str, int]` `black_wins`, `white_wins`, `draws` を含む辞書

---

#### append_record_list()

`Record` オブジェクトのリストを DB に保存します。内部で `DBRecordStore` を使い、`Record` を `Game`/`Kifu` に分解して SQLite に保存します。

```python
db_service.append_record_list(record_list, update=False)
```

- **record_list**: `Iterable[GameRecord | None]` 保存するレコードオブジェクトのリスト
- **update**: `bool` (デフォルト: `False`) 既存レコードの更新を有効化

---

#### delete_games_by_ids()

データベース ID を指定してゲームを削除します。

```python
deleted = db_service.delete_games_by_ids([1, 2, 3])
```

- **game_ids**: `Iterable[int]` 削除するゲームの ID リスト
- **戻り値**: `int` 削除された行数

---

#### delete_games_by_names()

ゲーム名を指定してゲームを削除します。

```python
deleted = db_service.delete_games_by_names(["game_001", "game_002"])
```

- **game_names**: `Iterable[str]` 削除するゲーム名のリスト
- **game_type**: `str | None` (デフォルト: `None`) スコープを限定するゲームタイプ
- **戻り値**: `int` 削除された行数

---

#### upsert_engine_artifact()

エンジンアーティファクトのメタデータを保存または更新します。

```python
entity = db_service.upsert_engine_artifact(snapshot)
```

- **snapshot**: `EngineArtifactSnapshot | None` エンジンアーティファクト情報
- **戻り値**: `EngineArtifact | None` ORM エンティティ

---

#### upsert_instance_spec()

インスタンス仕様情報を保存または更新します。

```python
entity = db_service.upsert_instance_spec(snapshot)
```

- **snapshot**: `InstanceSnapshot | None` インスタンス情報
- **戻り値**: `InstanceSpec | None` ORM エンティティ

---

#### record_game_participation()

完了した対局の参加メタデータを保存します。

```python
db_service.record_game_participation(game_id=1, participation=records)
```

- **game_id**: `int` 対局のデータベース ID
- **participation**: `Iterable[GameParticipationRecord]` 参加記録のリスト

---

#### get_instance_game_history()

指定インスタンスの対局履歴を取得します。

```python
history = db_service.get_instance_game_history("instance_001", limit=50)
```

- **instance_id**: `str` インスタンス ID
- **limit**: `int` (デフォルト: `50`) 取得する最大件数
- **offset**: `int` (デフォルト: `0`) 取得開始位置
- **戻り値**: `list[dict[str, Any]]` 対局履歴

---

#### close()

データベース接続を閉じます。

```python
db_service.close()
```

---

### レコード型

```python
from shogiarena.arena.services.persistence.records import (
    EngineArtifactSnapshot,
    InstanceSnapshot,
    GameParticipationRecord,
)
```

---

## 判定サービス

---

## AdjudicationConfig クラス

投了・最大手数などの判定設定です。`services/game_control` モジュールに属します。

```python
from shogiarena.arena.services.game_control.adjudication import (
    AdjudicationConfig,
    Adjudicator,
)

config = AdjudicationConfig(
    resign_enabled=True,
    resign_score_cp=800,
    resign_move_count=8,
    resign_two_sided=True,
    max_plies_enabled=True,
    max_plies=320,
)
```

- **resign_enabled**: `bool` (デフォルト: `True`) 投了判定を有効化
- **resign_score_cp**: `int` (デフォルト: `800`) 投了閾値（センチポーン、絶対値）
- **resign_move_count**: `int` (デフォルト: `8`) 投了に必要な連続手数
- **resign_two_sided**: `bool` (デフォルト: `True`) 両側に投了チェックを適用
- **max_plies_enabled**: `bool` (デフォルト: `True`) 最大手数判定を有効化
- **max_plies**: `int` (デフォルト: `320`) 引き分けとする最大手数

---

## Adjudicator クラス

全種類の判定を統合するメインの判定サービスです。

```python
adjudicator = Adjudicator(config)
```

- **config**: `AdjudicationConfig` 判定設定

#### update()

判定状態を更新し、対局終了条件をチェックします。

```python
result = adjudicator.update(
    eval_cp=-900,
    score_type="cp",
    ply=150,
    side_to_move_is_black=True,
)
```

- **eval_cp**: `int | None` 評価値（センチポーン、正 = 先手有利）またはメイト距離
- **score_type**: `str | None` スコアの種類（`"cp"`, `"mate"`, `None`）
- **ply**: `int` 対局開始からの手数（0-based）
- **side_to_move_is_black**: `bool` 評価時に先手が手番だったか
- **戻り値**: `GameResult | None` 判定結果（継続の場合は `None`）

---

#### get_status_summary()

デバッグ・監視用のステータスサマリを取得します。

```python
summary = adjudicator.get_status_summary()
```

- **戻り値**: `dict[str, dict[str, int]]` ステータス情報

---

## アーティファクトサービス

---

## ArtifactResolver クラス

エンジンバイナリの解決とビルドを担当します。

```python
from shogiarena.arena.services.artifacts.resolver import ArtifactResolver, ArtifactId
```

#### resolve()

アーティファクトを解決してパスを返します。

```python
path = resolver.resolve(artifact, overrides=None)
```

- **artifact**: アーティファクト指定
- **overrides**: オプションの上書き設定
- **戻り値**: 解決されたパス

---

### Builder ヘルパー

```python
from shogiarena.arena.services.artifacts.builder import run_make
```

#### run_make()

YaneuraOu 形式の make を実行します。

```python
run_make(...)
```

---

## 使用例

### SPRT テスト

```python
from shogiarena.arena.services.statistics.sprt import Sprt, SprtDecision
from shogiarena.utils.types import GameResult

sprt = Sprt(elo0=0, elo1=5, alpha=0.05, beta=0.05)

# 対局結果を追加
results = [GameResult.BLACK_WIN, GameResult.WHITE_WIN, GameResult.DRAW_BY_REPETITION]
for result in results:
    status = sprt.add_game_result(result)
    if status.decision != SprtDecision.CONTINUE:
        print(f"判定: {status.decision}")
        print(f"LLR: {status.llr:.2f}")
        break
```

### レーティング計算

```python
from shogiarena.arena.services.statistics.rating_service import EloRatingService
from shogiarena.utils.types import GameResult

service = EloRatingService(initial_rating=1500.0, k_factor=16.0)

# 対局後のレーティングを更新
new_black, new_white = service.update_ratings(
    black_player="Engine1",
    white_player="Engine2",
    game_result=GameResult.BLACK_WIN,
)
print(f"Engine1: {new_black:.0f}, Engine2: {new_white:.0f}")
```

### DB サービス

```python
from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.db.factory import SQLiteShogiDBFactory

factory = SQLiteShogiDBFactory("game.db")

with ArenaDBService(factory) as db_service:
    # 結果を取得
    counts = db_service.get_game_result_counts()
    print(f"結果: {counts}")

    # 全対局を取得（ページネーション付き）
    games = db_service.get_all_games(offset=0, limit=50)
    print(f"対局数: {len(games)}")
```

---

## 関連ドキュメント

- [サービスアーキテクチャ](../technical/services.md) - サービス層の詳細
