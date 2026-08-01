# SPRT / SPSA

統計検定用の `run sprt` と、パラメータチューニング用の `run spsa` の使い方を説明します。

## SPRT

SPRT は、2 つのエンジン差が十分に大きいかを逐次的に判定します。

```bash
cp examples/configs/run/sprt/example.yaml sprt.yaml
shogiarena run sprt sprt.yaml
```

最小構成:

```yaml
experiment_name: "sprt-test"

engines:
  - name: "Modified"
    engine_path: "modified.yaml"
  - name: "Baseline"
    engine_path: "baseline.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 1000
  num_parallel: 4

rules:
  time_control:
    time_ms: 60000
    increment_ms: 1000

sprt:
  tested_engine: "Modified"
  model: gsprt-pentanomial-v1
  elo0: 0.0
  elo1: 5.0
  alpha: 0.05
  beta: 0.05
  min_games: 60
  max_games: 1000
```

`sprt.model` には `gsprt-trinomial-v1` または `gsprt-pentanomial-v1` を指定します。
既定は `gsprt-trinomial-v1` です。
`sprt.tested_engine` が H1（改善候補）側です。省略時は互換性のため `engines` の先頭を使いますが、設定ファイルでは明示してください。
`max_games` と `tournament.games_per_pair` を両方指定する場合、値は一致していなければなりません。
同じ局面を先後入れ替えで 2 局ずつ使う設定なら、`gsprt-pentanomial-v1` を選ぶと color-reversed pair の五項分布を使って LLR を更新できます。

この運用では `rules.initial_positions.flip_policy: pair_both` を使います。
USI opening line file から到達 SFEN を作る tournament/SPRT 設定では `source_format: usi_line` を使えます。
`sync_scope: pair` を guard として添えると、誤って `pair_both` 以外にした設定を検証時に止められます。

`min_games` は、LLR が境界を超えていても指定局数までは判定を確定しない gate です。
短いテストで偶然の序盤結果に引っ張られたくない場合に使います。
pentanomial model は有限標本の厳密検定ではなく Brownian/正規近似です。
実装は最低 30 ペアに加え、分散を観測データから推定できるまで判定を保留しますが、これは指定した alpha/beta の有限標本保証を意味しません。

SPRT の resume hash には model と definition が含まれます。
途中 run を再開するとき、異なる `sprt.model` や復元不能な SPRT state では fail closed し、古い統計 model として暗黙に再開することはありません。

結果の読み方は次の通りです。

- **H0 棄却**：Modified が設定した差以上に強い可能性が高い
- **H0 受容**：設定した差は確認できない
- **`max_games` 到達**：結論に必要な対局数が足りない

OpenBench / ShogiBench へ提出する場合は、run 設定の `openbench` ブロックを使います。

## SPSA

SPSA は、1 つのエンジン設定から plus/minus バリアントを生成し、対局結果からパラメータを更新します。

> **1.2.0 SPSA実行契約**
>
> Observation分類、transactional ledger、LTC accepted baseline、resume契約のproduction
> qualificationを完了し、Local/Remote SPSAを利用できます。
> 1.2.0のledgerを持つ中断runはresumeできます。
> Legacy JSON-only archiveは1.2.0で表示・resume・importできないため、ShogiArena 1.1.0で閲覧してください。

### Validation level

- `--validate-only`はrun configに加え、SPSA spaceのload/normalize、initial positions fileの存在と非空、instance sourceとengine `instance_id`の照合、artifact IDの構文検証まで行います。
- `--dry-run`は同じ検証に加え、default composition root、run directoryの解決、local engine configのfixed-option preflightまで進みます。preflight artifactは一時ディレクトリだけに作成し、run archive、engine process、network accessは開始しません。
- Artifactのfetch/build、`usi_tunables` handshake、Remote接続は`--dry-run`の対象外です。
- Remote runはdispatch前にnetwork、worker platform、deployment、artifactを検証します。
  `--dry-run`はnetworkへ接続しないため、このRemote preflightを実行しません。

```bash
cp examples/configs/run/spsa/example.yaml spsa.yaml
shogiarena run spsa spsa.yaml
```

現行の SPSA 設定では `engines` は 1 エントリのみです。
チューニング対象のパラメータ一覧は、`spsa.space` に SPSA space 定義ファイルとして渡します。

```yaml
experiment_name: "my-spsa"

engines:
  - engine_path: "engine.yaml"
    options:
      Threads: 2
      USI_Hash: 1024
    go_options:
      nodes: 1000000

rules:
  time_control:
    node_limit: 1000000
  initial_positions:
    type: file
    source: "./data/openings/pair_positions.sfen"
    source_format: sfen
    flip_policy: pair_both
    sync_scope: pair
  adjudication:
    enable_max_plies: true
    max_plies: 320

spsa:
  space: "examples/configs/resources/spsa/rshogi-az-mcts.yaml"
  num_updates: 200
  pairs_per_update: 2
  inflight_factor: 6
  num_parallel: 4
  algorithm:
    name: classic
    alpha: 0.602
    gamma: 0.101
    A:
      mode: ratio
      value: 0.1
  variants:
    pairing: plus_minus
    crn: true
    integer_rounding: stochastic
    apply:
      clear_hash: true
      after_setoption: isready

dashboard:
  enabled: true
  api_port: 8080
```

## SPSA の主要フィールド

| フィールド | 説明 |
| --- | --- |
| `space` | 調整対象パラメータを定義する SPSA space ファイル |
| `num_updates` | 更新回数 |
| `pairs_per_update` | 1 更新あたりの対局ペア数 |
| `inflight_factor` | 同一update内で同時に進めるpair投入量の係数。update間はbarrierで逐次 |
| `algorithm` | SPSA のゲインスケジュール |
| `variants.crn` | Common Random Numbers による分散削減 |
| `variants.integer_rounding` | 整数パラメータの丸め方式 |
| `variants.apply.clear_hash` | variant適用後に置換表を初期化する。`true`ではtuned engineの`Clear Hash` buttonが必須 |
| `num_parallel` | SPSA 用の並列数 |

`variants.apply.clear_hash`の既定値は`true`です。

この設定では、tuned engineが名前と大文字小文字を含めて`Clear Hash`というUSI buttonを公開しない場合、preflightで実行を拒否します。

これは再利用したengine processでplus variantの置換表をminus variantへ持ち越し、勾配測定へ方向性のある偏りを入れないための検査です。

### 削除済みキーの移行

| 削除済みキー | 移行先 |
| --- | --- |
| `spsa.update_mode` | 削除する。更新同期は常に barrier semantics で動作する |
| `spsa.variants.instance_affinity` | engine の `instance_id` または `system.instance_scheduling` を使う |
| `spsa.parameters_path` | versioned normalized space を `spsa.space` で指定する |

これらのキーは互換 parser を持たず、指定すると config validation で拒否されます。

## 開始局面

SPSA では `rules.initial_positions.type: file` を指定し、開始局面 SFEN リストを用意する運用を推奨します。
`flip_policy: pair_both` にすると、同じ entry の初期 SFEN を plus/minus の color-reversed pair に割り当てられるので、Common Random Numbers としてノイズを抑えられます。
`shogiarena run spsa spsa.yaml` で実行する場合、相対 `source` はその YAML ファイルの場所を基準に解決されます。

`sync_scope: pair` は SPSA の同期機構そのものではなく、`flip_policy: pair_both` を要求する意図明示の guard です。
USI opening line から始めたい場合は、line をあらかじめ SFEN リストへ変換して `source_format: sfen` として渡してください。
line 由来の指し手は対局前に SFEN へ反映されるだけで、棋譜に強制手として保存されません。

## ダッシュボード

`dashboard.enabled: true` の場合、`http://localhost:8080` で次を確認できます。

- 更新ごとの結果
- パラメータの現在値
- 勾配推定
- 収束状況
- 生成された対局一覧

## 実行結果

run ディレクトリには通常の `game.db`、`manifest.json`、`state.json` に加え、SPSA 固有のイベントやスナップショットが保存されます。
完了後も次のコマンドで再表示できます。

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

### Ledger、terminal、resume

新規runのoptimizer authorityは`<run-dir>/spsa/ledger.sqlite3`です。
`events.jsonl`、`current.json`、`index.json`はdashboard互換projectionであり、
resume元ではありません。Run seed、space/resume digest、parameter rounding、
pair assignment、accepted LTC baseline、terminal reasonはledgerとsealed manifestで検証します。

`completed`、`early_stopped`、`failed`は再開不可、`cancelled_resumable`だけが
同一contract検証後に再開可能です。Legacy JSON-only archiveは1.2.0での表示、resume、
silent ledger importを拒否します。閲覧にはShogiArena 1.1.0を使い、1.2.0で実行するときは
新しいrun directoryを指定してください。

Accepted updateの正本は`spsa/accepted-best.json`です。
Ledger commit ID、parameter wire value、LTC decision、manifest、tunable manifest、
baseline/tuned engineのprovenanceを保存するため、採用判断ではこのartifactを確認してください。

Dashboardはarchived runを変更しません。SPSA revision feedはledger revisionだけを通知し、
gap/reconnect時はREST snapshotを再取得します。Final terminal snapshotを取得してから接続を閉じます。

### Backup / restore

1. Runを停止し、worker jobがterminal/collectedであることを確認する。
2. Run directory全体を別pathへcopyし、少なくとも`manifest.json`、`game.db`、
   `spsa/ledger.sqlite3`、`completion_status.json`（存在する場合）のSHA-256を記録する。
3. Migrationや調査はcopy側だけで行い、source archiveを変更しない。
4. Restoreは変更済みdirectoryへ上書きせず、backupを新しいpathへcopyして開く。
5. Schema/version、manifest、resume/space digestが拒否された場合はversionをstampし直さず、
   そのschemaに対応するShogiArenaで確認するか、新しいrun directoryで開始する。

Ledger単体のcopyは`game.db`とのcross-store整合性を失うためbackupとして不十分です。

## 関連

- [内部技術: SPRT](../internals/sprt/index.md)
- [内部技術: SPSA](../internals/spsa/index.md)
- [ダッシュボード](dashboard.md)
