# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html),
except for the explicitly documented 1.2.0 breaking-change exception.

## [Unreleased]

## [1.2.1] - 2026-08-02

### Fixed

- `variants.apply.clear_hash: true`のSPSAで、再利用するbaselineとtunedの全engine roleが`Clear Hash` buttonを公開することを測定前に検証するようにした。
- `state.json`が欠落してもledgerが存在するSPSA runをfresh runとして扱わず、sealed manifestを保持したままledger authorityからresume stateを再構築するようにした。
- LTCを間欠実行するSPSAで、`accepted-best.json`のparameter valueとwire valueを同じaccepted updateから生成するようにした。
- Remote worker bundleのresume拒否にexpected digest、actual digest、復旧方法を表示するようにした。
- Local実行でもsealed `minimum_worker_version`をengine起動前に検証するようにした。

1.2.0で作成したSPSA runのtunable handshakeに全engine roleの証跡がない場合、
1.2.1はresume時に全roleの`Clear Hash` preflightを実行します。
設定schemaとledger schemaの変更はありません。

## [1.2.0] - 2026-08-01

1.2.0はRemote実行とSPSA state modelを保守可能な単一契約へ収束させるため、
stable CLIと公開設定schemaへ意図的な破壊的変更を含む例外リリースです。
削除した契約のcompatibility shimは提供しません。

### Added

- **統一実行契約**: Local/Remoteが同じversioned `GameExecutionSpec`、timeout、result、provenance契約を使うようにした。
- **Remote worker bundle**: `shogiarena worker-bundle build`でwheel、lock、manifestからimmutable bundleを生成する。
  Installed wheelだけの環境でもsource checkoutへ依存せずbundleを構築でき、worker minimum versionを実行前に検証する。
- **Remote artifact配置**: endpoint-aware CASと、absolute path／SHA-256検証付き`preplaced` modeを追加した。
  `worker-bundle preplaced-map`はengineとfile／directory resourceのlogical ID mappingを生成する。
- **Durable Remote jobs**: 対局attemptごとのjob directory、atomic status/heartbeat/result、idempotent prepare/start/cancel/collect/ack、
  lease、orphan reaper、secret file transportを追加した。
- **SPSA ledger**: optimizer authorityをschema version 3のSQLite ledgerへ移し、sealed RNG、pair assignment、
  observation、LTC baseline/decision、terminal reason、revisionを永続化した。
- **Accepted-best artifact**: 採用済みparameterとledger、manifest、engine、tunable、LTC provenanceを
  `spsa/accepted-best.json`へ保存する。
- **SPSA dashboard**: archived zero-write projection、REST snapshot、durable revision feed、terminal eventを追加した。
- **Local platform contract**: Windows x86_64、Linux x86_64／arm64、macOS Intel／Apple Siliconを正しいprovenanceで表現する。

### Changed

- **Remote実行（破壊的変更）**: Remote workerをLinux x86_64に限定し、shared Git checkout、CWD推測、
  縮小Remote spec、固定`.tmp/spec.json`、旧worker protocolを削除した。
- **Remote provisioning（破壊的変更）**: `--provision`は`cas`または`preplaced`だけを受理する。
  未検証の`none`とmutableな`force`は削除した。
- **Remote scheduling（破壊的変更）**: assignmentは`system.instance_scheduling.policy`の
  `local`／`explicit`／`auto`で明示し、暗黙のlocal fallbackを行わない。
- **SPSA設定（破壊的変更）**: `spsa.update_mode`、`spsa.parameters_path`、
  `spsa.variants.instance_affinity`を削除し、update間は常にbarrier semanticsで実行する。
- **SPSA archive（破壊的変更）**: Legacy JSON-only archiveのresume、import、dashboard表示を削除した。
  Current ledger runは`cancelled_resumable`だけを同一contractで再開できる。
- **SPSA dashboard wire（破壊的変更）**: 旧SPSA payloadのSSE／WebSocketを削除し、
  ledger revision feedとREST snapshotへ統一した。
- **Instance設定（破壊的変更）**: `instances.yaml`の未知keyを無視せずfail closedで拒否する。
- **Resume identity**: worker bundle/deployment digestとminimum worker versionをsealed manifestとresume hashへ含める。

### Fixed

- Current ledger SPSA runをproduction CLIからcancel、resume、completeできるようにした。
- Crash後のSPSA hot journalをwritable recoveryしてからread-only authority validationへ進むようにした。
- Legacy SPSA archiveで未処理tracebackを出さず、既存bytesを変更しないactionable errorを返すようにした。
- Prepared Remote deploymentをendpoint内でreuseし、control failure時だけfull sealを再検証するようにした。
- 別のuv project内へインストールした場合も、利用者projectではなくinstalled ShogiArena distributionからworker bundleを構築するようにした。
- Remote directory artifact verificationをworker bundleと同じ`uv`／Python authorityへ統合した。
- Remote status pollingからworker Python起動を除去し、実測worker CPU占有を約99.9%削減した。
- Local artifact digestをstat identity単位でreuseし、2 engineの毎局hash時間を実測約75.9 msから約1.0 msへ削減した。
- SPSA variantをengine process startup keyから外し、4 updatesのgameplay processを8個から2個へ削減した。
- `variants.apply.clear_hash: true`でtuned engineが`Clear Hash` buttonを公開しない場合、測定開始前に拒否するようにした。
- `accepted-best.json`の補助投影失敗でledger resumeを停止せず、正本ledgerから再投影できるようにした。
- Dashboard terminal revisionがREST event projection cacheを無効化するようにした。

### Migration

- `spsa.update_mode`は削除し、設定から取り除く。1.2.0は常にupdate間barrierを使う。
- `spsa.parameters_path`は`spsa.space`へ置き換える。
- `spsa.variants.instance_affinity`はengineの`instance_id`または`system.instance_scheduling`へ置き換える。
- `--provision none`は全resourceを検証する`--provision preplaced`へ、`--provision force`はimmutableな`--provision cas`へ置き換える。
- Legacy JSON-only SPSA archiveはShogiArena 1.1.0で閲覧する。
  1.2.0では既存archiveを変更せず、新しいrun directoryから開始する。
- Remote workerはLinux x86_64へ移し、`worker-bundle`で生成したdeploymentを使う。
- `instances.yaml`のvalidation errorが示す未知keyを削除または現行fieldへ移す。

## [1.1.0] - 2026-07-26

### Added

- 時間切れの由来を**証拠**から分類し、engine 起因と断定できないものを無効局として扱うようにした。
  判定には、その手の締切（GameClock が確定した deadline）、ShogiArena が `bestmove` を最初に
  観測した時刻、event loop / thread の停滞を常時監視する watchdog の観測範囲を使う。
  超過分が重なった停滞より大きい場合だけ engine 起因の時間切れ負けとし、停滞だけで超過を
  説明できてしまう場合や観測範囲が欠けている場合は `unknown` として勝敗を付けない。
  締切より前に `bestmove` を観測できていた場合だけ `orchestrator_stall` と判定する。
  無効局は `GameResult.ERROR` として rating と SPRT の標本から除外する。
  有効化するのは arena / tournament 系のみで、SPSA は従来どおりの扱いを維持する。
- 無効な時間切れが閾値に達した場合に、新規対局の投入を止めるようにした。
  由来ごとに独立した閾値を持ち、閾値未満では run を継続して当該局を標本から除くだけにする。
  1回の停滞は並行中の全対局を同時に無効化しうるため、連続発生には早く反応する。
- run 終了時に `completion_status.json` を出力するようにした。
  `clean` / `with-anomalies` / `failed` の判定に加えて `termination_reason` を持ち、
  SPRT の正常な早期終了（`sprt-finished`）と異常な中断を区別できる。
  時間切れの由来別内訳と watchdog の停滞計測も含む。
  `status=failed` は「この run の結果を完了した測定として扱えない」という意味であり、
  異常終了とは限らない（利用者が停止した run も `failed` になる）。
  予定したが実施しなかった局数は `not_played` として記録する。
  中断された run では、後片付けの前に暫定 status を書いてから確定へ昇格させる。
  暫定のまま残った場合は `is_provisional` が `true` になり、後片付けに失敗した場合は
  `cleanup_error` を併記する。中断の理由そのものは後片付けの失敗で置き換えない。
- 時間切れの由来を `game_timeout_attribution` テーブルへ保存するようにした。
  純追加のテーブルなのでスキーマ版数は据え置きで、このテーブルを持たない既存の DB も
  引き続き読み書きできる。旧バージョンからも新しい DB を開ける。
- `EngineLifecycleEventName.state_changed` を追加した。
  `UsiEngineSession` の lifecycle handler は、process の起動・終了だけでなく engine の
  state 遷移ごとにも呼ばれるようになる。既存 handler の呼び出し回数が増えるため、
  handler 内で blocking work を行わないこと。未知の将来 event 名は無視できる実装にすること。

### Changed

- dashboard 有効時のエンジン入出力テレメトリのコストを削減した。
  エンジン状態のバッジは軽量なライフサイクルイベントで駆動し、生ログは購読者がいる対局にだけ
  流すようにした。指し手・消費時間・結果の記録は従来どおり欠落しない。
  24局の controlled A/B（いずれも dashboard 有効、WebSocket 未接続）では、
  `io.collect_raw_io` を on にした場合の所要時間比が 8.65 倍から 1.146 倍になった。
  これは headless の demand gate の効果であり、dashboard 無効時との比較ではない。
  実ブラウザで視聴した場合の比は本 CHANGELOG では主張しない。
- Live view の既定表示で raw I/O を購読しないようにした。
  kickoff 表示は lifecycle 由来の `engine_status` だけで成立させ、raw I/O の購読は
  利用者が明示的に生ログを開いた対局だけに限定する。
  既定の grid では並列数に関係なく購読数が 0 になる。

### Fixed

- tournament 経路で stall watchdog が起動していなかった問題を修正した。
  この修正により、時間切れの由来分類が実運用で初めて機能する。
- SPRT が結論に達した後、実行中だった対局が完了すると結論が取り消されることがあった問題を
  修正した。停止を決めた時点で標本を締め、それ以降に完了した対局は記録には残すが検定へは
  加えないようにした。締めた後に完了した局数は SPRT の status に `late_games` として出る。
  あわせて、停止を要求した後は空きが出ても新しい対局を開始しないようにした。
- 無効な時間切れの計数が resume で失われ、中断と再開を繰り返すと安全停止の閾値に
  到達しなくなる問題を修正した。計数は `state.json` へ保存し、保存が無い場合は
  `game.db` の記録から復元する。
- resume 時に、DB へ保存済みで `state.json` に未反映の対局を完了順に再生するようにした。
  SPRT の決着、決着後に完了した対局、安全停止の計数を復元し、再開直後に不要な対局を
  投入しない。通常の一時停止と run を終了する停止も別の制御状態として扱う。
- timeout 後に回収した `bestmove` の最初の観測時刻と観測根拠が分類処理まで届かず、
  engine 起因の時間切れを `unknown` に誤分類できる問題を修正した。
- finalization または service cleanup が失敗した場合に、正常完了を示す marker が残る問題を
  修正した。dashboard、OpenBench、DB、棋譜 writer は一つの停止処理が失敗しても残りを停止し、
  最初の cleanup failure を `cleanup-error` として記録する。`completed.flag` は作成しない。
- `game_timeout_attribution` が無い DB では、read-only 媒体だけを互換モードで開くようにした。
  writable DB の lock、I/O error、disk full、破損を table 不在として握り潰さず、
  旧 DB と新 DB の読み書き互換を保ったまま異常を fail closed にする。

## [1.0.2] - 2026-07-24

dashboard 無効の長時間 run で event loop が秒単位で停止し、進行中の対局が一斉に時間切れ負けとして
記録される問題を修正した。dummy engine による 500 局の controlled A/B は次のとおり。

| Arm | wall time | loop lag p95 | 500ms超 lag | timeout / ERROR | progress queue 最大 | RSS 最大 |
|---|---:|---:|---:|---:|---:|---:|
| 1.0.0 相当 | 795.23s | 1466.95ms | 400 | 45 | 406,755 | 301.1MiB |
| 主因のみ無効化 | 356.04s | 70.58ms | 0 | 0 | 445,791 | 327.1MiB |
| 1.0.2 | 344.72s | 49.84ms | 0 | 0 | 0 | 115.1MiB |

影響を受けた run の対局結果には、engine の実力差ではなく orchestrator の停止に由来する
時間切れ負けが含まれる。該当 run の Elo / SPRT の結論は信頼できないため、再実行を推奨する。

### Known Issues
- **dashboard 有効時の raw engine I/O 配信コスト**: engine の state が変わるたびに worker snapshot 全体が再構築され、8〜11 回走査される。snapshot は手数に比例する履歴配列と最大400件の I/O tail を含むため、1局あたりのコストが手数の二乗に近い形で増える。24局の A/B では engine I/O listener を無効にした場合と比べて wall time が 5.72 倍だった。1.0.0 から存在する問題で本リリースでの悪化はない（137.87s→137.85s）。event loop の停止は最大 262ms に留まり時間切れ負けは発生しないため、対局結果は歪まない。次リリースで対応する。

### Fixed
- **長時間 run で対局が一斉に時間切れ負けになる問題**: engine options の callback から起動される summary 更新が dashboard の有効・無効に関わらず配線されており、完了済み全対局を入力とする BTD 最尤推定を event loop 上で同期実行していた。1回の推定コストが完了局数に比例するため、run 後半で event loop が秒単位で停止し、進行中の全対局が同時に bestmove 待ちの deadline を割っていた。500局の A/B 計測では loop lag p95 が 1466.95ms→70.58ms、timeout が 45局→0局、wall time が 2.23倍高速になった。dashboard 無効時は summary 更新を配線せず、有効時も options が実際に変化した場合だけ起動して更新を coalesce する。
- **BTD 推定が完了局数に比例して遅くなる問題**: 最適化ループが1対局1要素のまま最大2000 iteration 走査していた。対数尤度と勾配の各項は対局を (先手, 後手) のペアを通じてのみ参照するため、ペア単位のカウント集約は厳密な同値変換である。集約により推定コストは distinct pairing 数に固定され、局数に依存しなくなった（2 engine / 1000局で 1.30s→0.004s）。
- **handshake timeout が時間切れ負けとして記録される問題**: `go` 送信前の `usi` / `isready` timeout も game loop の広い `except TimeoutError` に捕捉され、`*_WIN_BY_TIMEOUT` として Elo / SPRT へ架空の勝敗が算入されていた。0手 timeout の主な発生源でもある。専用の `UsiHandshakeTimeoutError` を導入し、時間切れ負けではなく ERROR として扱う（rating / SPRT の sample から除外される）。`go` 送信後に bestmove が返らない本来の timeout は従来どおり時間切れ負けとする。
- **progress queue が無制限に蓄積する問題**: queue と producer は dashboard の有無に関わらず接続される一方、consumer は dashboard 有効時しか起動しないため、USI I/O 1行ごとの JSON 文字列が回収されずに溜まり続けていた（500局で 445,791件 / RSS 343MB）。consumer が起動しないときは producer 側へ queue を渡さず、engine I/O listener も登録しない。consumer が遅れた場合は engine I/O event のみ破棄し、対局の状態を持つ move / clock / result は破棄しない。

### Changed
- **USI I/O log handler の dispatch**: progress 用 handler がコルーチンを返す通常の `def` だったため、`asyncio.to_thread` 経由で USI 1行ごとに thread 往復が発生していた。`async def` にして event loop 上で直接実行する。

## [1.0.0] - 2026-07-21

### Added
- **Release artifact contract**: wheel / sdistへdashboard bundle、`py.typed`、第三者noticeを同梱し、installed-wheel runtime / consumer typing smokeと公開example全件dry-runをCIへ追加した。
- **Persistence versioning**: SQLite `PRAGMA user_version`と物理schema検査を追加し、完全互換の未version DBだけをstampするようにした。
- **Security policy**: `SECURITY.md`とGitHub private vulnerability reportへの導線を追加した。
- **Book move-source metadata**: 対局 DB に `game_move.move_source` / `game_move.book_hit` と record metadata attributes を保存し、`move_source` / `book_hit` / `_arena_schedule` を record roundtrip で保持するようにした。
- **Dashboard Book Pairs report**: `GET /api/book/pairs` と Book タブの `Pairs` subview を追加し、先後入れ替えペアごとの book prefix、first diff、match rate、measurement status を確認できるようにした。
- **Pair-synchronized opening line configuration**: `rules.initial_positions.source_format` (`auto` / `sfen` / `usi_line`)、`sync_scope`、`preserve_line_metadata` を追加し、USI line file から到達 SFEN を生成して `pair_both` のペアへ同じ opening entry を渡せるようにした。

### Changed
- **v1 public API contract（破壊的変更）**: stable面をCLI、公開設定schema、通常利用向けengine/tournament APIに限定した。`run_tournament()`は`TournamentRunResult | None`を返す。高度なcomposition / runner / storage面はprovisionalとして明示した。
- **公開dataclassのkeyword-only化（破壊的変更）**: public facadeが公開する全dataclassをkeyword-onlyにした。対象は`UsiEngineConfig`、`GameSpec`、`UsiThinkRequest`、`UsiOption`、`UsiIoEvent`、`EngineLifecycleEvent`、`EngineProcessInfo`、`UsiAnalyzeItem`、`UsiAnalyzePosition`、`UsiAnalyzeResetPolicy`、`UsiMateResult`、`PonderHitTimings`、`TournamentResults`、`SprtResult`。field追加を破壊的変更にしないための措置で、位置引数で構築していたコードはキーワード引数へ変更が必要。`FilesystemRunStorage`と`DefaultRoot`はprovisionalかつ単純な構築子のため対象外とした。
- **stable戻り値のfield型を公開**: `TournamentRunResult`のfield型である`TournamentResults`、`EngineWdlCounts`、`SprtResult`、`SprtDecision`、`JsonValue`を`shogiarena.tournament`から、`JsonValue`を`shogiarena.engine`からimportできるようにした。従来は結果へ型注釈を付けるために`shogiarena._core`を直接importする必要があった。
- **Public config strictness（破壊的変更）**: tournament設定とengine設定のuser-authored modelは未知keyを拒否する。`options` を `optiosn` と綴り間違えた設定が黙って無視され、指定したはずのUSI optionが無効のまま対局が走る事故を防ぐ。`system`の未知keyを`system.extras`へ保存する拡張契約は維持した。SPSA設定の未知keyは現状warningに留まり、1.xの間にfail closedへ揃える。
- **rsshogi 1.0.x への移行**: 依存を `rshogi-py-avx2==0.10.3` から `rsshogi>=1.0.1,<2` に更新した（crates.io `rshogi` → `rsshogi`、PyPI `rshogi-py(-avx2)` → `rsshogi(-avx2)` のリネームに追従）。AVX2 専用ビルドではなく portable ビルドを既定依存にしたため、Windows x86_64、Linux x86_64 / arm64、macOS Intel / Apple Silicon に wheel が提供される（Windows on ARM は対象外）。x86_64 で AVX2 版が必要な場合は `rsshogi` を `rsshogi-avx2` に差し替える（import 名が同じなので同時インストール不可）。import パッケージ名も `rshogi` → `rsshogi` に変わり、record API の `GameRecord` → `Record`、`MoveRecord` → `MoveEntry`、`MoveEngineInfo` → `EngineInfo`、`SpecialMoveRecord` → `SpecialMoveEntry`、`GameRecordMetadata` → `RecordMetadata`、`Board.legal_moves_full()` → `legal_moves_move32()` に追従した。
- **sbinpack 出力が v2 になった**: rsshogi 1.0.0 の `to_sbinpack()` はマジック `SBN2` を出力する（旧 `SBIN`）。v1 を書き出すオプションはないため、学習データを読む側は v2 対応が必要。
- **開発環境を Windows ネイティブへ移行**: `.devcontainer/` を削除し、`make ci` / `make ci-develop` が Windows 上で通るようにした。`make clean` を `tools/clean_caches.py` に置換して `find` / `rm` 依存を解消し、`MDBOOK` の既定値から POSIX シェル依存を除いた。`.python-version` は CI に合わせて `3.11` に緩和した。
- **`github_token` を settings ファイルに保存しなくなった**: `settings.yaml` は `github_token_env`（環境変数名。既定 `SHOGIARENA_GITHUB_TOKEN`）だけを持ち、token 本体は環境変数から解決する（`openbench.password_env` と同じ方式）。秘密を持たなくなったため `settings.yaml` の `chmod(0o600)` も廃止した（Windows では chmod が read-only ビットしか反映せず強制もできなかった）。CLI の `--github-token` は `--github-token-env` に置き換わった。
- **リリース系スクリプトを PowerShell へ移行**: `scripts/export_public_snapshot.sh` / `promote_release_main.sh` を `.ps1` に置き換えた。export には `-DryRun` とコミット前の dev-only パス検査を追加し、`scripts/check_public_export.ps1`（`make check-public-export`）で一時リポジトリ上の dry-run による自己検証を行えるようにした。環境確認用に `make check-env` を追加。
- **依存ライブラリを更新・整理**: numpy 1.26.4→2.4.6、pytest 8.4.1→9.1.1、ruff 0.12.10→0.15.22、ty 0.0.11→0.0.61、aiohttp 3.12.15→3.14.1、pydantic 2.12.5→2.13.4ほかを更新した。production未使用のmatplotlib / pandas / tqdmをruntime依存から除外し、型stubとpytest-covをdev依存へ分離した。
- **`CLAUDE.md` を `AGENTS.md` へのポインタに縮小**: 両ファイルが 98% 重複していたため、`CLAUDE.md` は `@AGENTS.md` を読み込む薄い入口にした。`agent-docs/rules/README.md` を新設。
- **`.vscode/` と `release-notes/` を公開リポジトリから除外**: エディタ個人設定とリリースノート下書きは dev 専用にした。リリースの公開正本は `CHANGELOG.md` と GitHub Release で、`release-notes/*.txt` は export コミットメッセージの入力として dev 側に残る。
- **リリースワークフローを硬化**: タグ・`pyproject.toml`・`__init__.py` の version 一致を検証する `validate-version` ジョブを追加し、PyPI publish を `PYPI_TOKEN` から Trusted Publishing (OIDC) へ移行、release 系 actions を SHA pin した。workflow の既定権限は `contents: read` に下げた。長期の API トークンは保持しない。
- **Opening book fallback handling**: YaneuraOu 互換の `.db` 指定に対し、隣接する `.ybb` が存在する場合は preflight / provenance / remote book transfer で実体 book として扱うようにした。
- **Opening line docs and examples**: tournament / SPRT / SPSA の設定例とユーザーガイドに、pair-synchronized opening line の使い方、`sync_scope` は guard であること、engine-owned book と混ぜないための `BookFile: no_book` 推奨を追記した。

### Fixed
- **Resume / crash consistency**: tournament / SPRT / SPSAの復元順序とstate検証を修正し、破損・hash不一致・DB統計不一致で既存artifactを上書きしない。OpenBenchの加算POSTは送信前markerを永続化し、曖昧な応答を自動再送しない。state fileの書き込みは rename 前に fsync するため、電源断で内容だけが失われることがない。
- **OpenBench送信済みrunの破棄を拒否**: `--no-resume` は state.json を消す前に送信実績を検査する。OpenBenchの加算APIは idempotency key を持たないため、送信済みカウンタを消して0から再送するとサーバ側の集計が二重になる。
- **read-only媒体のrun artifactを閲覧できない問題**: 読み取り経路が version の刻印と WAL への切り替えを前提にしており、書き込めない媒体上のarchived runを開けなかった。検証が通っていれば読み取りを成功させる。破損DBの sqlite 例外も復旧手順付きの `StoreSchemaError` に包む。
- **archived dashboardのinstancesタブが常にエラーになる問題**: read-only時に instances のルートを丸ごと未登録にしており、GETまで404になっていた。読み取りルートは登録し、mutation は security middleware が 403 で止める。閲覧しただけでローカルインスタンスは生成しない。
- **USI transcriptのサイレント打ち切り**: 出力上限が打ち切りマーカーより小さいと、打ち切りの事実自体が記録されなかった。
- **`--github-token` が秘密を settings.yaml へ書き込んでいた**: v1.0.0 で `--github-token` は `--github-token-env` に置き換わったが、argparse の前方一致により `--github-token ghp_xxx` がエラーにならず `github_token_env: ghp_xxx` として保存されていた。トークン文字列が環境変数名として平文で残り、参照先の環境変数も存在しないため private repo 解決は無言で失敗する。`--github-token` を明示的に受け付けて移行先を案内するエラーで停止するようにした。
- **配布物への開発用ファイル混入**: wheel に dashboard frontend の TypeScript ソース 350 ファイル（2.8MB、テスト 37 本を含む）が同梱されていた。ランタイムが読むのは `frontend/index.html` だけなので除外し、wheel は 983→633 ファイルになった。sdist には `htmlcov`、`.claude/settings.json`、`.vscode/settings.json`、`AGENTS.md`、`release-notes` が含まれていた。sdist の対象を許可リストで宣言し、dev-only パスの否定検査を release gate に追加した。public export の秘密パス除外もリポジトリ直下限定だったものを全階層へ拡張した。
- **SPRT / SPSA correctness**: pentanomialの極小標本判定を防ぎ、tested engine順序、試合数設定、LTC revert後parameterを単一契約へ揃えた。
- **Dashboard / runtime safety**: dashboardをloopback限定・Host/Origin/content-type検査付きにし、archived dashboardをread-only化した。remote setupの共有Futureとengine cleanupをcancel-safeにした。
- **DB operation isolation**: operationごとにcommit / rollback / scoped-session removalを保証し、失敗transactionが次のasync workerへ漏れる問題を修正した。
- **Public examples and packaging**: 読み込めなかった設定example、dashboard static path、frontend build metadata、release workflow順序を修正した。
- **Windows ネイティブでの動作**: `signal.SIGHUP` を無条件参照して CLI が Windows で `AttributeError` になる問題、`GIT_ASKPASS` ヘルパが `#!/bin/sh` のみで Windows から起動できない問題を修正した。frontend の `vitest.config.ts` は `include` glob が `path.join()` で `\` 区切りになり Windows でテストが 1 件もマッチしていなかったため、相対 glob に修正した。architecture lint の出力パスは `as_posix()` に統一した。
- **SPSA 設定の `crn` / `snap_float_to_step` が無視されていた**: `SpsaVariantsConfig` / `SpsaRunConfig` / `SpsaVariantApplyConfig` は alias 付きフィールドを持つが `populate_by_name` が未設定だったため、パーサがフィールド名で渡した値を Pydantic が黙って捨てていた。`spsa.variants.crn: false` を指定しても CRN が有効なままになる。
- **中断ゲーム再開時に `round` が `None` になりうる問題**: `entry.get("round", default)` はキーが存在して値が `None` のときに default を返さないため、`round: null` を含む再開ステートで `GameSpec.round_num=None` が構築されていた。
- **`promote_release_main.ps1` が親なしコミットを作っていた**: PowerShell が `git commit-tree ... -p HEAD` の `-p` を共通パラメータ `-PipelineVariable` として前方一致で解釈し、フラグと値ごと git に渡らなくなっていた。結果として release commit が root commit になり `main` の履歴が切れる。`"-p"` と明示的にクォートして修正し、`make check-promote-release` で回帰を検出できるようにした。
- **Book prefix diagnostics correctness**: `book_hit` の未知値を `False` に潰さず未測定として扱い、`out of book` などの否定的な `info string` を `move_source=book` と誤判定しないようにした。
- **Opening line metadata propagation**: state-store 経由の schedule generation でも `generate_entries()` の metadata を保持し、`preserve_line_metadata=false` のときは USI line の指し手列を record metadata に残さないようにした。
- **Tournament failure records**: plain tournament の game failure isolation で作る ERROR record に schedule metadata を付けつつ、従来の static helper 互換と未初期化 schedule state の fallback を保つようにした。

## [0.5.4]

### Fixed
- **Strict `rules.time_control` validation**: `TimeControlLimits` の unknown key を fail-fast で拒否し、`depth` / `nodes` / `byoyomi` などの誤用は `depth_limit` / `node_limit` / `byoyomi_ms` など正しいキーを示すエラーにした。`validate-only` で設定 typo を検出し、実行時に全局 ERROR になる前に停止する。
- **Engine runtime configuration alignment**: エンジン設定の環境変数キーを `environment` に統一し、旧 `env` を明示エラーにした。CLI から直接指定した engine binary/path でも `environment`、`engine_args`、`build_options` などの runtime 設定を生成済み engine YAML に保持するよう修正した。
- **Engine-level `go_options` validation**: engine config / tournament config / SPSA config の `go_options` を共通正規化し、エンジン単位の既定値として安全に扱える `depth` / `nodes` のみに限定した。`movetime`、`btime`、`wtime`、`byoyomi`、`infinite` などの時間制御は `rules.time_control` または明示的な探索 request 側で扱うようにし、対局の持ち時間管理との競合を防いだ。

### Changed
- **Book documentation refresh**: `docs/book/` を `v0.5.0` 以降の機能に合わせて更新し、GSPRT / pentanomial SPRT、engine opening book、remote book resource、Book tab、engine lifecycle、resource capacity preflight、engine wall time metrics、public USI engine session API の説明を現行仕様へ揃えた。
- **Public API and CLI documentation**: `shogiarena.engine` の `UsiEngineSession`、typed `UsiIoEvent`、`UsiEvalValue`、batch analysis、option validation policy、`get_usi_options()` の戻り値変更を明記し、`run tournament` / `run analyze` / `run mate` / `results summary` の CLI 説明を補強した。
- **Book structure and troubleshooting cleanup**: `SUMMARY.md` に既存の内部技術ページを収載し、古い run directory、dashboard stream endpoint、`startup_timeout_sec` などの旧記述を現在の設定名・出力レイアウトへ更新した。

## [0.5.3]

### Added
- **Public USI engine session API**: `create_engine()` / `create_engine_from_mapping()` の戻り型を `UsiEngineSession` として公開し、engine lifecycle、`think()`、option application、IO logging、batch analysis を private implementation に依存せず型付けできるようにした。
- **Structured USI analysis data**: `UsiEvalValue.kind/value/is_mate/is_cp/as_dict()` と `UsiThinkResult.select_pv()` を追加し、score 文字列の再 parse や downstream 独自 PV selection helper を不要にした。
- **Engine process and lifecycle observability**: `EngineProcessInfo`、`process_info`、lifecycle handler、typed `UsiIoEvent` を公開し、profiler attach、trace capture、diagnostics collection を public API で扱えるようにした。
- **Lightweight fixed-position analysis API**: `iter_analyze_positions()` / `analyze_positions()`、per-position reset policy、failure collection policy を追加し、単一 engine process を再利用する fixed-position capture を ShogiArena runtime policy として実行できるようにした。
- **USI option validation policy**: `strict`（既定）、`warn`、`raw`、`allow_unlisted_combo_value` を追加し、file/path 系 combo option などの custom value を用途に応じて許可できるようにした。

### Changed
- **Public engine API（破壊的変更）**: `get_usi_options()` は JSON snapshot ではなく `Mapping[str, UsiOption]` を返す。tournament metadata 用 JSON snapshot は内部の match adapter で明示変換する。
- **USI IO event contract（破壊的変更）**: `UsiIoEvent` は Mapping 互換 key（`dir` / `ts` / `state`）を提供せず、`direction` / `line` / `phase` / `timestamp_ms` の typed field を使う。stderr は `direction="stderr"`、prefix なしの raw line として扱う。

## [0.5.2]

### Fixed
- **Engine wall time persistence**: `GameRecord.moves` getter 経由の clone 更新を避け、`engine_wall_time_ms` を `GameRecord` payload に入れて再構築するよう修正。tournament DB の実指し手行で `game_move.engine_wall_time_ms` が保存されるようにした。

## [0.5.1]

### Added
- **Engine throughput wall time metrics**: tournament DB / live payload / game detail に `engine_wall_time_ms`（配列では `engine_wall_times_ms`）を追加し、既存の clock-charged `wall_time_ms` と engine I/O 窓を分離。result summary では wall NPS の既定 wall time field が `engine_wall_time_ms` であることを明示する。

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
- **Accidental public implementations**: `AsyncUsiEngine`、`AsyncUsiProcess`、`SpawnerBackedUSIBridge`、不正なconstructorを露出していた`RunStorage`をpublic facadeから削除した。
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

[Unreleased]: https://github.com/nyoki-mtl/ShogiArena/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/nyoki-mtl/ShogiArena/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/nyoki-mtl/ShogiArena/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/nyoki-mtl/ShogiArena/compare/v1.0.0...v1.0.2
[1.0.0]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.5.4...v1.0.0
[0.5.4]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.5.0
[0.4.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.4.0
[0.3.1]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.3.1
[0.3.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.3.0
