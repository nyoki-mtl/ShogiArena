# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0]

### Added
- **GSPRT / pentanomial SPRT**: SPRT の LLR 計算を fishtest-style の GSPRT 実装へ更新し、従来の per-game trinomial に加えて color-reversed pair を使う pentanomial model（`gsprt-pentanomial-v1`）を追加。tournament runner、SPSA LTC regression、dashboard replay が同じ model / min-games gating に従うようにした。
- **Engine opening book support**: エンジン内蔵定跡（`BookDir` + `BookFile`）を path resource として扱い、YaneuraOu 互換の composite path 解決、起動前 preflight、rshogi diagnostics、book provenance / fingerprint 記録を追加。既定の `BookFile: no_book` 方針は維持。
- **Dashboard Book tab**: 保存済み対局から book fingerprint / path 単位の使用状況、勝率、先後別集計、engine 別内訳、out-of-book ply 分布を確認できる Book タブと `GET /api/book/summary` を追加。out-of-book は「実着手が指定 book の候補手集合に含まれたか」の観測指標であり、エンジンが book 由来で指した断定ではない。
- **Remote book resource handling**: remote 実行時に book resource を扱うための配布・書き換え基盤を追加。大型 book は既定で自動転送せず、`SHOGIARENA_REMOTE_BOOK_TRANSFER` による opt-in / preplaced 運用と fingerprint 照合を使えるようにした。
- **Engine lifecycle policy**: `tournament.engine_lifecycle` を追加。既定の `reuse` は従来どおり pool へ返却し、`per_game` では各対局後に両エンジンプロセスを閉じる。
- **Parallel resource capacity preflight**: `system.resource_capacity_preflight` を追加。`tournament.num_parallel` 分の pending games が instance の `slots` / `max_engines` に収まらない場合、既定で対局開始前に明示エラーにする（`warn` / `off` で緩和可能）。
- **Recovery and reproducibility improvements**: binary records index の crash-safe recovery、resume 時の DB からの Elo rebuild、seeded fair color assignment と `color_policy_version` 記録、runtime metadata cache signature の強化を追加。

### Changed
- **SPRT resume contract（互換影響）**: SPRT model / definition を resume hash に含め、別 model や復元不能な SPRT state での resume は fail closed する。0.4.0 以前の SPRT run を 0.5.0 の統計 model として暗黙再開しない。
- **Dashboard / artifact contracts（破壊的変更）**: project-owned JSON / dashboard wire key を `snake_case` へ統一し、内部境界の旧 camelCase dual-read を削除。古い dashboard artifact は現在の schema で再生成または再実行が必要になる場合がある。
- **DB schema terminology（破壊的変更）**: DB table/column を将棋用語の英語正本へ寄せ、`kifu` table を `game_move`、`init_position_sfen` column を `initial_position_sfen` へ rename。既存 DB は schema guard により再生成が必要。
- **Engine config naming（破壊的変更）**: `working_dir` 系の受け口を `working_directory` へ統一。設定・metadata・docs も `working_directory` を正本にした。
- **Overlay options format（破壊的変更）**: USI option overlay YAML は `options:` ブロック配下に書く形式へ統一。旧 flat top-level form はサイレント無視せず明示エラーにする。
- **Opening / book path handling**: `BookFile` は scalar path option ではなく `BookDir + BookFile` の composite resource として解決する。相対 `BookFile` を cwd 基準で誤解決しないよう、engine へ渡す USI option は engine 互換の組表現を維持する。
- **Docs and examples**: public docs を再編し、設定例を `examples/configs/` 配下へ集約。開発用 task / architecture / rule docs は public export から除外される dev-only 資料として整理した。

### Fixed
- **USI runtime reliability**: late `bestmove` / `stop()` / `gameover` の順序、ponder / shutdown、cancel / timeout cleanup、runtime metadata cache、EnginePool の instance capacity 予約を修正。
- **Game result and adjudication correctness**: non-decided result を draw / loss に折り込む誤り、非決着結果の `gameover` token 化、adjudication / result determination の複数不具合を修正。
- **SPSA safety**: option assignment、draw-aware win rate、`min_games` gated SPRT decision、gain schedule docs と実装説明の不一致、不要な `a0` / mobility knob 表示を整理。
- **Dashboard correctness and safety**: XSS / lifecycle / accessibility / build 問題、stats 集計、games-listing service failure の HTTP 500 化、corrupt run artifact をゼロ扱いする挙動、Book tab の `out_of_book` UI 反映を修正。
- **Records / persistence robustness**: binary writer lifecycle、PSV validation、records manifest / index handling、corrupt `state.json` / game.db / SPRT restore error の fail-closed 化を強化。
- **OpenBench totals**: crash-only / non-decided games を提出 totals から除外。

### Removed
- `FallbackInstance` と runtime factory fallback を削除し、composition root / CLI から実 local instance を注入する形へ統一。
- `SpsaUpdateDeltaService`、古い manifest helper、dead `byte_count` manifest alias、dashboard の camelCase clock fallback、旧 flat overlay 受け口など、未使用・重複・互換のためだけの surface を削除。
- `README_ja.md` と旧 `configs/` 配下の例を削除し、`README.md` と `examples/configs/` に集約。

## [0.4.0]

### Added
- **SPSA space protocol runtime**: チューニング対象を宣言する `space` スペックを導入。pair ベースのスケジュール、plus/minus 直接ペアリング（plus 視点 `score_sum`）、不変な space spec / runtime artifact、共有乱数による plus/minus 整数丸めを実装。
- **Run artifact contract / two-phase manifest sealing**: run の入力（frozen inputs）と解決済み provenance を分離し、`manifest.json` を `inputs_only` → `provenance_sealed` の二段階で封印。`canonical_json_bytes`（キーソート正規化）と domain-separated ハッシュにより決定的・改竄検知可能な artifact を生成。manifest/summary/results の書き込みを `write_json_atomic` 経由のアトミック書き込みに統一。
- **Offline result summary CLI**: `shogiarena results summary <run-dir|game.db>` を追加。永続化済み対局から WDL・勝率・引分率・Wilson 信頼区間・失敗集計を JSON/CSV/text で出力。`shogiarena results verify-provenance` で sealed manifest の provenance を検証。
- **Run failure diagnostics / path preflight**: エンジン起動・isready・think 等の失敗を構造化レコード（`failures/`）として永続化し、startup 失敗を診断 payload 化。トーナメント設定の path オプションに opt-in の事前検証（preflight）を追加。
- **USI transcripts**: per-game の USI protocol transcript 出力を追加（`logging.usi_transcript` で有効化、`logging.usi_transcript_detail: commands | commands_and_info`）。`<run_dir>/transcripts/game-<id>-{black,white}.log` に保存。
- **`replay-position` CLI**: 保存済み棋譜から任意 ply の局面を USI エンジンで再探索。fresh / transcript ベースの history-aware replay / compare モードに対応。
- **Generate resume**: `run generate` を正式に resume 対応。`game.db` の `game_type=generate` 完了局面を信頼源として復元し、records 出力は `records_index.jsonl` による game_id 単位の台帳で重複追記を防止。

### Changed
- **SPSA 設定（破壊的変更）**: チューニング対象の指定を `space`（space スペックファイル）に統一。旧 `parameters_path` / `tune_file` は廃止。
- **Run 出力レイアウト（破壊的変更）**: run artifact 契約を刷新。旧 `run_metadata.json` / `run_manifest.json` は出力せず、`manifest.json`（two-phase sealing）に統一。後方互換シムは提供しない。
- **Records 出力（破壊的変更）**: バイナリ records manifest を schema v2 に更新し、`records_index.jsonl` と byte range 台帳を追加。旧 `records_manifest.json` / 台帳なし出力の migration は提供しない。
- **DB query contract（破壊的変更）**: `get_games_with_players` は `game_type` の明示指定を必須化。暗黙の `arena` 既定を廃止し、arena/tournament と generate の完了集合が混ざらないようにする。
- **依存更新**: `rshogi-py-avx2` を 0.7.5 から 0.10.3 へ更新し、`push_move` / `push_move32` / `SpecialMoveRecord.from_result` / `GameRecord.from_usi_main_line` などの helper へ移行。
- **開発ツール**: `make check` の高速化。

### Fixed
- **Engine runtime**: エンジン起動失敗時の `failure_phase` を `close()` がステートマシンを巻き戻す前に捕捉。isready タイムアウトが `engine_start` に誤分類される問題を修正。
- **SPSA**: 整数パラメータの stochastic rounding を `math.floor(value + rng.random())` に統一し、負値のゼロ方向バイアスと pair builder 経路との不一致を解消。`crn=false` の opening 割当を mini-batch 順で事前抽選し、async スケジューリング順に依存しない決定的・再現可能な割当に修正。
- **CLI（results）**: `--confidence` の範囲外値や `game.db` の破損 `game_result` 値による `ValueError` を `CliArgumentError` に変換し、生のトレースバックではなくクリーンなエラー（exit 1）にする。
- **CLI（replay-position）**: `--infinite` を `--timeout` 必須化し、timeout 後に `stop()` で bestmove を回収（従来は無限待機/未処理例外）。`--searchmoves` の不正 USI move も `CliArgumentError` 化。
- **Game execution**: `finally` の cleanup 例外が進行中の例外（`CancelledError` を含む）を上書きしないようガードし、失敗レコードの永続化失敗が元例外をマスクしないようにする。
- **Generate records**: fresh start で run-dir 配下の records 出力を cleanup し、外部 `records_output.output_dir` の既存出力へサイレント追記しない。DB 完了済みだが records 未書込の局面は `game.db` から backfill し、台帳に載っていない末尾 bytes は resume 前に truncate する。records 書込済みだが DB 未完了の orphan は完了扱いせず、明示エラーにする。
- **Release workflow**: public release workflow を冪等化。

### Removed
- 未使用の dashboard SPRT config payload を削除。

## [0.3.1]

### Fixed
- **Engine runtime**: Decoupled USI I/O log handlers from the monitor loop so heavy trace consumers no longer delay `bestmove` parsing and `stop()` recovery.
- **Testing**: Added regression coverage for blocked synchronous I/O log handlers during `think()` and timeout recovery paths.

## [0.3.0]

*v0.1.x からの大規模リファクタリングにより、すべてのモジュール構成・API・設定形式が刷新されました。
以前のバージョンとの後方互換性はありません。*

### Changed
- **Architecture**: Bounded context パターンによる `_core/` 4層構造 (contexts, interfaces, platform, shared) へ全面移行
- **Public API**: `shogiarena.engine`, `shogiarena.tournament`, `shogiarena.cli`, `shogiarena.composition` を正式な公開面として整理
- **CLI**: `shogiarena config init` / `run tournament` / `run sprt` / `run spsa` / `dashboard serve` 等のサブコマンド体系に再編
- **Dashboard**: WebSocket/SSE ベースのリアルタイム更新、モジュール分割されたフロントエンド (vanilla TypeScript + Vite)
- **Engine runtime**: USI プロトコル実装の刷新、ポンダー・詰将棋探索ステートの追加
- **Tournament**: ラウンドロビン・ガントレット・セルフプレイ方式のスケジューラ
- **SPSA**: ゲインスケジュール、LTC 回帰テスト、分散削減テクニックの統合
- **Rating**: Bradley-Terry-Davidson モデルによる Elo 推定、五項分布 SPRT
- **Config**: Pydantic ベースの型安全な設定システム、artifact ビルド・リモート実行対応
- **Documentation**: mdBook ベースの包括的ドキュメント整備

[Unreleased]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.5.0
[0.4.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.4.0
[0.3.1]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.3.1
[0.3.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.3.0
