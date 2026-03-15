# Services と Utilities

ShogiArena の横断ロジックは、単一の `services/` ディレクトリにまとまっているわけではありません。現在は `_core.contexts.*` と `_core.shared.kernel` と `_core.platform` に責務ごとに分かれています。

## 主要カテゴリ

- 統計
  Elo / SPRT / BTD / pentanomial
- 永続化
  DB adapter / record store / run storage
- game control
  adjudication / time control / result helper
- settings / path / host probe
  runtime の共通 helper

## どこにあるか

- 統計 service
  `shogiarena._core.contexts.game_session.application`
- 純粋 helper / 型 / small utilities
  `shogiarena._core.shared.kernel`
- DB / settings / records / host probe
  `shogiarena._core.platform`

## 具体例

### 統計

- [`EloRatingService`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/application/elo_rating_service.py)
- [`Sprt`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/application/sprt_service.py)
- [`BTDEstimator`](/workspaces/ShogiArena/src/shogiarena/_core/shared/kernel/statistics/btd_rating.py)
- [`compute_pentanomial()`](/workspaces/ShogiArena/src/shogiarena/_core/shared/kernel/statistics/pentanomial.py)

### 永続化

- [`RunStorage`](/workspaces/ShogiArena/src/shogiarena/_core/contexts/game_session/adapters/run_storage.py)
- [`ArenaDBAdapter`](/workspaces/ShogiArena/src/shogiarena/_core/platform/db/store/arena_db_adapter.py)
- [`DBRecordStore`](/workspaces/ShogiArena/src/shogiarena/_core/platform/db/store/record_store.py)

### utility / kernel

- [`resolve_path_like()`](/workspaces/ShogiArena/src/shogiarena/_core/shared/kernel/paths.py)
- [`timeout_win_result()`](/workspaces/ShogiArena/src/shogiarena/_core/shared/kernel/game_results.py)
- [`detect_target_cpu()`](/workspaces/ShogiArena/src/shogiarena/_core/platform/host_probe/cpu_detection.py)
- [`project_dirs`](/workspaces/ShogiArena/src/shogiarena/_core/platform/settings/project_dirs.py)

## 設計意図

- service は「複数 use case から呼ばれる stateful / orchestration 寄りの部品」
- kernel helper は「小さく純粋で再利用される部品」
- platform は「外部資源や OS / DB / filesystem との境界」

この分割により、utility という曖昧な箱を増やさずに責務を追いやすくしています。

## 関連ページ

- [Services API](../api/services.md)
- [Utils API](../api/utils.md)
- [DB API](../api/db.md)
