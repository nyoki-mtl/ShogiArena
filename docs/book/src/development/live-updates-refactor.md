# Live updates handlers 分割メモ（SPSA/LIVE）

> **ステータス: 完了済み** — 以下の分割リファクタリングは実施済みです。

- `src/shogiarena/_core/interfaces/dashboard/frontend/src/modules/live/services/updates/handlers.ts` は
  ワーカー更新、SPSA進捗通知、saved games、時計更新など多責務を抱え 800+ 行に達していた。
  共有 util（safeClone、snapshot cache 等）が内側に閉じており再利用しづらかった。
- 実施した分割
  1. ルーター: `handlers.ts` には EventEmitter への配線と delegator のみを残した。
  2. ワーカー系: `workerUpdates.ts`（clock/update/gameComplete/catchup）。
  3. SPSA系: `spsaProgress.ts`（SPSA_SUMMARY_PROGRESS_EVENT など）。
  4. Saved games 系: `savedGames.ts`（ライブビュー snapshot と completed cache 処理）。
  5. 共通 util: `./common.ts` に safeClone、completedSnapshotCache、out-of-order ログ抑制を移動した。
- テスト方針
  - 各分割モジュールに委譲スモークテストを置き、`connect/pause/disconnect` 相当のメソッドが呼ばれることを確認。
  - スナップショット依存は避け、純関数ユーティリティは小さな入力で検証する。
