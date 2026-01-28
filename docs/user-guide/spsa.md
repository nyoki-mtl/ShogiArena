# SPSA Tuning

Shogi Arena は SPSA (Simultaneous Perturbation Stochastic Approximation) アルゴリズムを用いたエンジンパラメータの自動チューニングをサポートしています。

## 実行コマンド

`run` コマンドの `spsa` サブコマンドで実行します。

```bash
shogiarena run spsa configs/spsa/my_tuning.yaml [options]
```

## 設定ファイル (SpsaConfig)

SPSA 用の設定ファイルはトップレベルに `spsa` ブロックを持ちます。

### 基本構造

```yaml
experiment_name: "tuning_exp1"

engines:
  - name: "TargetEngine"
    artifact: "YaneuraOu/abcdef1"
    build_options:
      target_cpu: "ZEN3"
      tune_file: "param_definitions.txt" # チューニング対象の定義

spsa:
  parameters_path: "params.json" # 現在のパラメータ値
  num_updates: 1000              # 更新回数
  num_parallel: 8                # 並列実行数
  
  # SPSA ハイパーパラメータ
  A: 0
  alpha: 0.602
  gamma: 0.101
  mobility: 0.002
  
  # LTC Regression（オプション）
  ltc_regression:
    enabled: true
    every_n_updates: 50
    total_pairs: 400

rules:
  time_control:
    time_ms: 5000
    increment_ms: 100
  initial_positions:
    type: file
    source: "configs/openings/standard.sfen"
    flip_policy: pair_both
```

### エンジン設定

SPSA モードでは `engines` リストに**1つのエンジンのみ**を指定します。システム内部で「ベースライン（変更前）」と「Tuned（変更後）」の2つに複製され、対戦が行われます。

- **tune_file**: `build_options` 内で必須です。どのパラメータをチューニングするか定義したファイルを指定します。

### SPSA パラメータ (`spsa`)

| フィールド | 説明 |
| --- | --- |
| `parameters_path` | パラメータの現在値を保存する JSON ファイルへのパス。 |
| `num_updates` | 総更新ステップ数。 |
| `inflight_factor` | 並列実行の係数（デフォルト 4）。勾配推定のために多くの対局を並行して流します。 |
| `A`, `alpha`, `gamma` | SPSA アルゴリズムの減衰係数。 |
| `mobility` | パラメータの変動幅の基準。 |

### LTC Regression (`ltc_regression`)

チューニング中に定期的に検証対局（LTC: Long Time Control）を行い、性能低下を防ぐ機能です。

- `every_n_updates`: 何ステップごとに検証するか。
- `total_pairs`: 検証対局数。
- `pass_criteria`: 通過条件（`max_elo_drop` など）。

## 実行結果

SPSA の実行結果は `output_dir/spsa/<config_stem>-<hash8>/YYYYMMDDHHmmSS/` に保存されます。

- **params.json**: 最新のパラメータ値が随時書き込まれます。
- **history.csv**: 各ステップの勾配、対局結果、パラメータの変化履歴。
- **dashboard**: SPSA の収束状況を確認できます（`http://localhost:8080/index.html`）。
