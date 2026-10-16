# SPRT / SPSA

変更前後の強さを比べるにはSPRT、パラメータを調整するにはSPSAを使います。

## SPRT

SPRTは、二つのエンジンの強さの差を、対局結果が増えるたびに検定します。

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
`sprt.tested_engine`には改善候補のエンジン名を指定します。
省略すると`engines`の先頭が使われます。
`max_games` と `tournament.games_per_pair` を両方指定する場合、値は一致していなければなりません。
同じ局面を先後入れ替えで 2 局ずつ使う設定なら、`gsprt-pentanomial-v1` を選ぶと、先後を入れ替えた対局ペアの五項分布を使ってLLRを更新できます。

この運用では `rules.initial_positions.flip_policy: pair_both` を使います。
開始局面をUSI形式の指し手列で指定するには、`source_format: usi_line`を使います。
`sync_scope: pair`も指定すると、`pair_both`以外の設定を検証時に検出できます。

`min_games` は、LLR が境界を超えていても指定局数までは判定を確定しない設定です。
短いテストで偶然の序盤結果に引っ張られたくない場合に使います。
五項分布モデルの判定はBrownian/正規近似に基づきます。
実装は最低 30 ペアに加え、分散を観測データから推定できるまで判定を保留しますが、これは指定した alpha/beta の有限標本保証を意味しません。

再開する場合は、同じ検定モデルと設定を使ってください。
検定モデルが異なる場合や、保存した検定状態を復元できない場合は再開できません。

結果の読み方は次の通りです。

- **H0 棄却**：Modified が設定した差以上に強い可能性が高い
- **H0 受容**：設定した差は確認できない
- **`max_games` 到達**：結論に必要な対局数が足りない

OpenBench / ShogiBench へ提出する場合は、対局設定の `openbench` ブロックを使います。

## SPSA

SPSAは、一つのエンジンのパラメータを増減させた二つの設定で対局し、その結果から値を更新します。

```bash
cp examples/configs/run/spsa/example.yaml spsa.yaml
shogiarena run spsa spsa.yaml
```

`engines`には、調整するエンジンを一つ指定します。
この設定から、比較基準のエンジンとパラメータを調整するエンジンが作られます。
チューニング対象のパラメータ一覧は、`spsa.space` に パラメータ定義ファイルとして渡します。

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
| `space` | 調整対象パラメータを定義する パラメータ定義ファイル |
| `num_updates` | 更新回数 |
| `pairs_per_update` | 1 更新あたりの対局ペア数 |
| `inflight_factor` | 一回の更新で同時に投入する対局ペア数の係数。更新は順番に行う |
| `algorithm` | SPSA のゲインスケジュール |
| `variants.crn` | 旧設定との互換用。値にかかわらずペア内で同じ局面を使用する |
| `variants.integer_rounding` | 整数パラメータの丸め方式 |
| `variants.apply.clear_hash` | 設定変更後に置換表を初期化する。`true`ではエンジンの`Clear Hash`ボタンが必要 |
| `num_parallel` | SPSA 用の並列数 |
| `derived_json_min_interval_s` | 派生 JSON（`spsa/current.json` など）を再生成する最小間隔[秒]。既定 300 |

`variants.apply.clear_hash`の既定値は`true`です。

この設定では、エンジンが`Clear Hash`という名前のUSIボタンに対応していない場合、実行前の検査で停止します。

パラメータを変更する前の置換表が残り、比較結果に偏りが出るのを防ぐためです。

## 開始局面

SPSA では `rules.initial_positions.type: file` を指定し、開始局面 SFEN リストを用意する運用を推奨します。
`plus_minus`では、ペアごとに同じ開始局面を使い、plus/minusの先後を入れ替えて2局対戦します。
開始局面は、更新番号とペア番号に対応する乱数で、入力ファイル全体から復元抽出します。
同じ更新内や別の更新で同じ局面が選ばれることがあります。
割当は`run_seed`、run ID、局面ファイルの内容と順序、割当規則の版が同じなら再現でき、中断・resumeや並列実行順に依存しません。

1.3.1以降、`variants.crn`は互換用の入力です。
`true`でも`false`でも、更新ごとの抽選とペア内の局面共有・先後反転を行います。
1.3.0以前のrunは局面割当規則が異なるためresumeできません。
既存runを保存したまま、別のrunディレクトリで新規実行してください。

`shogiarena run spsa spsa.yaml` で実行する場合、相対 `source` はその YAML ファイルの場所を基準に解決されます。

`sync_scope: pair`を指定すると、`flip_policy: pair_both`になっているか検証されます。
USI形式の指し手列から始めたい場合は、指し手列をあらかじめ SFEN リストへ変換して `source_format: sfen` として渡してください。
指し手列は対局前に SFEN へ反映されるだけで、棋譜に強制手として保存されません。

## ダッシュボード

`dashboard.enabled: true` の場合、`http://localhost:8080` で次を確認できます。

- 更新ごとの結果
- パラメータの現在値
- 勾配推定
- 収束状況
- 生成された対局一覧

## 実行結果

実行結果のディレクトリには通常の `game.db`、`manifest.json`、`state.json` に加え、SPSA 固有のイベントやスナップショットが保存されます。
完了後も次のコマンドで再表示できます。

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

### 結果と再開

SPSAの更新履歴は`spsa/ledger.sqlite3`に保存されます。
ダッシュボード用の`current.json`や`events.jsonl`からは再開しません。
中断状態が`cancelled_resumable`の実行だけが、同じ設定とエンジンで再開できます。
完了、早期終了、失敗した実行をやり直す場合は、新しい保存先を指定してください。

採用されたパラメータは`spsa/accepted-best.json`に保存されます。
パラメータ値とLTCの判定を確認するには、このファイルを使ってください。

### 実行前の確認

`--validate-only`で設定、パラメータ定義、開始局面ファイル、エンジンの実行先を検証できます。
`--dry-run`では、これらに加えてローカルエンジンの固定オプションを検査します。
どちらもエンジンの起動、ファイルの取得やビルド、SSH接続は行いません。
リモート実行先やエンジンが実際に使えるかの検査は、実行開始時に行います。

### バックアップ

実行を停止し、リモートのジョブも終了したことを確認してから、保存先ディレクトリ全体をコピーしてください。
`game.db`と`spsa/ledger.sqlite3`は一緒に保存する必要があるため、更新履歴のDBだけではバックアップになりません。
復元するときは新しいディレクトリへコピーし、元の保存先への上書きを避けてください。

## 関連

- [内部技術: SPRT](../internals/sprt/index.md)
- [内部技術: SPSA](../internals/spsa/index.md)
- [ダッシュボード](dashboard.md)
