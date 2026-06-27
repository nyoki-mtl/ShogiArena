# SPRT / SPSA

このページでは、統計検定用の `run sprt` と、パラメータチューニング用の `run spsa` をまとめます。

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
  - name: "Baseline"
    engine_path: "baseline.yaml"
  - name: "Modified"
    engine_path: "modified.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 2
  num_parallel: 4

rules:
  time_control:
    time_ms: 60000
    increment_ms: 1000

sprt:
  model: gsprt-pentanomial-v1
  elo0: 0.0
  elo1: 5.0
  alpha: 0.05
  beta: 0.05
  min_games: 0
  max_games: 1000
```

`sprt.model` は `gsprt-trinomial-v1` または `gsprt-pentanomial-v1` です。既定は `gsprt-trinomial-v1` ですが、同じ局面を先後入れ替えで 2 局ずつ使う設定では `gsprt-pentanomial-v1` を選ぶと、color-reversed pair の五項分布を使って LLR を更新できます。

`min_games` は、LLR が境界を超えていても指定局数までは判定を確定しない gate です。短いテストで偶然の序盤結果に引っ張られたくない場合に使います。

SPRT の resume hash には model / definition が含まれます。途中 run を再開する場合、異なる `sprt.model` や復元不能な SPRT state では fail closed し、古い統計 model として暗黙に再開しません。

結果の目安:

- H0 棄却: Modified が設定した差以上に強い可能性が高い
- H0 受容: 設定した差は確認できない
- `max_games` 到達: 結論に必要な対局数が足りない

OpenBench / ShogiBench へ提出する場合は、run 設定の `openbench` ブロックを使います。

## SPSA

SPSA は、1 つのエンジン設定から plus/minus バリアントを生成し、対局結果からパラメータを更新します。

```bash
cp examples/configs/run/spsa/example.yaml spsa.yaml
shogiarena run spsa spsa.yaml
```

現行の SPSA 設定では `engines` は 1 エントリのみです。チューニング対象のパラメータ一覧は `spsa.space` で SPSA space 定義ファイルとして指定します。

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
    source: "./data/initial_sfens/start_sfens_ply24.txt"
    flip_policy: pair_both
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
| `inflight_factor` | 先行投入する更新数の係数 |
| `algorithm` | SPSA のゲインスケジュール |
| `variants.crn` | Common Random Numbers による分散削減 |
| `variants.integer_rounding` | 整数パラメータの丸め方式 |
| `num_parallel` | SPSA 用の並列数 |

## 開始局面

SPSA では `rules.initial_positions.type: file` を指定し、開始局面リストを用意する運用を推奨します。`flip_policy: pair_both` にすると同じ局面を先後入れ替えで使いやすく、ノイズを抑えられます。

## ダッシュボード

`dashboard.enabled: true` の場合、`http://localhost:8080` で次を確認できます。

- 更新ごとの結果
- パラメータの現在値
- 勾配推定
- 収束状況
- 生成された対局一覧

## 実行結果

run ディレクトリには通常の `game.db`、`manifest.json`、`state.json` に加え、SPSA 固有のイベントやスナップショットが保存されます。完了後も次のコマンドで再表示できます。

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

## 関連

- [内部技術: SPRT](../internals/sprt/index.md)
- [内部技術: SPSA](../internals/spsa/index.md)
- [ダッシュボード](dashboard.md)
