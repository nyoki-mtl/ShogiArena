# SPSA（同時摂動確率近似法）

> **前提知識**：[Elo レーティング](../elo/index.md)、[SPRT](../sprt/index.md)

## このページの要点

- SPSA は**すべてのパラメータを同時に摂動**して勾配を推定する確率的最適化アルゴリズム
- パラメータが \\(p\\) 個あっても、1 回の更新にわずか **2 組のペアゲーム**で勾配を推定できる
- Rademacher 摂動（±1）を使い、各パラメータを ±step で同時に変化させる
- 将棋エンジンのパラメータチューニングに適しており、Stockfish の SPSA チューニングにも採用されている

## SPSA を選ぶ理由

将棋エンジンには、評価関数の重み、探索パラメータ、枝刈りの閾値など、数十から数百のパラメータがあります。
これらを対局で最適化しようとすると、次の四つが同時に立ちはだかります。

1. **目的関数が解析的でない**：勝率は閉形式で表現できず、実際に対局しないと評価できない
2. **評価にコストがかかる**：1 回の評価（対局）に数秒から数分かかる
3. **ノイズが大きい**：対局結果は確率的であり、同じパラメータでも結果がばらつく
4. **パラメータ数が多い**：個別に 1 パラメータずつ最適化するのは非効率

SPSA は、この四つすべてに同時に対応します。

### 他のアプローチとの比較

| 手法 | 1 回あたりの評価数 | ノイズ耐性 | 適用条件 |
|:---|:---:|:---:|:---|
| グリッドサーチ | \\(n^p\\) | 低い | パラメータ数が少ない場合のみ |
| 有限差分法 | \\(2p\\) | 中 | パラメータ数が少ない場合 |
| **SPSA** | **2** | **高い** | **パラメータ数に依存しない** |
| ベイズ最適化 | 1 | 高い | サロゲートモデルの構築が可能な場合 |

グリッドサーチと有限差分法の評価数はパラメータ数 \\(p\\) とともに増えますが、SPSA は 2 のまま変わりません。
評価コストをパラメータ数から切り離せる点が、SPSA の最大の強みです。
ベイズ最適化も評価数は少なく済みますが、目的関数を近似するサロゲートモデルを構築できることが前提になります。

## アルゴリズムの概要

SPSA の 1 回の更新サイクルは、以下の 5 ステップで構成されます。

```text
┌──────────────────────────────────────────────────┐
│  ステップ 1: Rademacher 摂動 ε の生成             │
│    ε_i ∈ {-1, +1}  (各パラメータに独立に)         │
│                                                   │
│  ステップ 2: ゲインの計算                          │
│    a_k, c_k  (反復回数 k に応じた減衰)             │
│                                                   │
│  ステップ 3: パラメータの摂動                      │
│    θ⁺ = θ + c_k · ε                              │
│    θ⁻ = θ - c_k · ε                              │
│                                                   │
│  ステップ 4: ゲームの実行                          │
│    s⁺ = score(θ⁺)                                │
│    s⁻ = score(θ⁻)                                │
│                                                   │
│  ステップ 5: 勾配推定とパラメータ更新              │
│    θ_i ← θ_i + a_k · δ_i · (s⁺ - s⁻) / 2       │
└──────────────────────────────────────────────────┘
```

### 数学的定式化

パラメータベクトル \\(\boldsymbol{\theta} \in \mathbb{R}^p\\) を最適化します。

目的関数 \\(L(\boldsymbol{\theta})\\) は、パラメータ \\(\boldsymbol{\theta}\\) での期待勝率です。
反復 \\(k\\) における各パラメータ \\(i\\) の更新式は次のとおりです。

\\[
\theta_i^{(k+1)} = \theta_i^{(k)} + a_k \cdot \delta_i \cdot \frac{s^+ - s^-}{2}
\\]

記号の意味は次のとおりです。

- \\(a_k\\)：ステップサイズゲイン（[ゲインスケジュール](./gain-schedule.md)で詳述）
- \\(\delta_i\\)：パラメータ固有の学習率スケール
- \\(s^+, s^-\\)：摂動パラメータでの対局スコア

## Rademacher 摂動

SPSA は、すべてのパラメータを 1 回の摂動でまとめて動かします。
各パラメータの向きは、±1 を等確率で取る **Rademacher 分布**から独立に引きます。

\\[
\varepsilon_i \sim \text{Rademacher} = \begin{cases} +1 & \text{確率} \; 1/2 \\\\ -1 & \text{確率} \; 1/2 \end{cases}
\\]

```python
C = [0.0 if p.not_used else
     (p.step * (1.0 if rng.randint(0, 1) else -1.0))
     for p in params]
```

### なぜ Rademacher 分布か

摂動の分布は自由に選べるわけではありません。
Spall (1992) は、次の 2 条件を満たす分布でなければ勾配推定が成立しないことを示しています。

1. 平均ゼロ：\\(E[\varepsilon_i] = 0\\)
2. 有界な逆モーメント：\\(E[1/\varepsilon_i^2] < \infty\\)

勾配推定では摂動値で割るため、\\(\varepsilon_i\\) が 0 に近い値を取りうる分布は条件 2 を破ります。
正規分布が使えないのはこのためです（\\(1/\varepsilon\\) の分散が発散する）。
Rademacher 分布では \\(1/\varepsilon_i^2\\) が常に 1 なので、条件 2 は自動的に満たされます。

## 勾配の推定

同時摂動による勾配推定は次式で書けます。

\\[
\hat{g}_i = \frac{L(\boldsymbol{\theta} + c_k \boldsymbol{\varepsilon}) - L(\boldsymbol{\theta} - c_k \boldsymbol{\varepsilon})}{2 c_k \varepsilon_i}
\\]

これを、パラメータを 1 個ずつ動かす有限差分法と比べます。

```text
有限差分法: パラメータ i だけを ±c で変化させて勾配を推定
  → 2p 回の評価が必要（p パラメータの場合）
  → 各パラメータごとに独立に推定

SPSA: すべてのパラメータを同時に ±c·ε で変化させて勾配を推定
  → 2 回の評価で十分
  → ノイズが大きいが、反復平均で収束
```

有限差分法は各パラメータを 1 個ずつ動かすので、パラメータが増えれば評価数もそのまま増えます。
SPSA は全パラメータをまとめて動かした 1 回の差分を、\\(\varepsilon_i\\) で割ることで各成分に振り分けます。
1 回ぶんの推定は粗くなりますが、摂動の向きが毎回引き直されるため、反復を重ねると誤差が平均されて真の勾配に向かいます。

## ShogiArena での実装

### パラメータの定義

各パラメータは `ParamEntry` で定義されます。

```python
@dataclass
class ParamEntry:
    name: str       # パラメータ名
    type: str       # "int" または "float"
    v: float        # 現在の値
    min: float      # 最小値
    max: float      # 最大値
    step: float     # 離散化ステップ / 摂動の基本単位
    delta: float    # パラメータ固有の学習率スケール
    comment: str    # コメント
    not_used: bool  # 無視フラグ
```

### パラメータファイル（CSV 形式）

```csv
Contempt, int, 100, 0, 200, 1, 0.002, // 形勢判断の偏り
NullMovePruning, int, 3, 1, 5, 1, 0.005, // null move 深度削減
LMRBase, float, 1.5, 0.5, 3.0, 0.1, 0.003, // Late Move Reduction の基本値
```

各列の意味:

| 列 | 意味 | 例 |
|:---|:---|:---|
| `name` | パラメータ名 | `Contempt` |
| `type` | 型（int/float） | `int` |
| `v` | 現在の値 | `100` |
| `min` | 最小値 | `0` |
| `max` | 最大値 | `200` |
| `step` | 摂動の基本単位 | `1` |
| `delta` | 学習率スケール | `0.002` |
| `comment` | コメント | `// 形勢判断の偏り` |

### 1 回の更新の流れ

```python
async def _run_one_spsa_update(self, update_idx, params, sfens):
    # 1. パラメータ単位のゲイン c_i / r_i を算出（step/delta/A/alpha/gamma から）
    schedule = compute_classic_schedule_point(
        params=params, num_updates=N, pairs_per_update=m, update_idx=update_idx,
        alpha=alpha, gamma=gamma, a_mode=A.mode, a_value=A.value, int_ck_floor=...,
    )
    # c_i = step_i · (k_total / k_pair)^γ  (int は int_ck_floor で下限クランプ)

    # 2. Rademacher の向き flip ∈ {+1, -1} と摂動（[min, max] にクランプ）
    flips = [0 if p.is_not_used else (1 if rng.randint(0, 1) else -1) for p in params]
    tuned_plus  = [p.value + flip * schedule.c[p.name] for p, flip in ...]
    tuned_minus = [p.value - flip * schedule.c[p.name] for p, flip in ...]

    # 3. バッチ（m ペア）を実行し、スコアを集計（CRN あり/なし）
    step = score_sum - s_minus          # = Σ s⁺ − Σ s⁻

    # 4. パラメータ更新（グローバル学習率スカラは無い）
    for i, p in enumerate(params):
        delta_theta = schedule.r[p.name] * schedule.c[p.name] * step * flips[i]
        p.value = quantize_value(p, p.value + delta_theta)
```

## 設定例

実際の設定スキーマ（`spsa` ブロック、`algorithm` / `variants` ネスト、`space` spec への参照）については
[SPSA Tuning](../../user-guide/spsa.md) を参照してください。以下は本章で説明した各概念が設定のどこに対応するかを示す抜粋です。

```yaml
spsa:
  space: "examples/configs/resources/spsa/rshogi-az-mcts.yaml"  # チューニング対象パラメータの定義
  num_updates: 1000        # 更新回数
  pairs_per_update: 4      # 1 更新あたりの対局ペア数（バッチサイズ）
  inflight_factor: 8       # 先行投入する更新バッチ数

  algorithm:               # ゲインスケジュール
    alpha: 0.602           # ステップサイズ減衰指数
    gamma: 0.101           # 摂動スケール減衰指数
    A:                     # 安定化項
      mode: ratio
      value: 0.1

  variants:                # 分散削減
    crn: true              # Common Random Numbers

  early_stop:              # 早期終了（delta_norm が閾値を下回ったら停止）
    type: "delta_norm"
    threshold: 0.001
```

## Early Stopping

更新量が十分小さくなったら、最適化を早期終了できます。

\\[
\|\Delta\boldsymbol{\theta}\| = \sqrt{\sum_i \Delta\theta_i^2} < \text{threshold}
\\]

```python
delta_norm = sqrt(sum(delta_i ** 2 for delta_i in deltas))
if self.config.early_stop and delta_norm < threshold:
    self._stop_event.set()  # 最適化を停止
```

## 実装リファレンス

| ファイル | クラス/関数 | 役割 |
|---------|----------|------|
| `_core/contexts/spsa/adapters/orchestrator.py` | `SpsaOrchestrator` | SPSA 実行の本体 |
| `_core/contexts/spsa/domain/spsa_models.py` | `ParamEntry` | パラメータ定義 |
| `_core/contexts/spsa/application/param_io.py` | `read_params()` / `write_params()` | パラメータの I/O |
| `_core/contexts/spsa/application/param_io.py` | `quantize_value()` | 値の量子化とクランプ |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `SpsaRunConfig` | SPSA 設定スキーマ |
| `_core/contexts/spsa/adapters/runner.py` | `SpsaRunner` | SPSA ランナー |

## 読了順序

- **[勾配推定と摂動](./gradient.md)**：勾配推定の数学的詳細と量子化の扱い
- **[ゲインスケジュール](./gain-schedule.md)**：収束を制御する減衰系列の設計
- **[ノイズと高次手法の限界](./noise-and-higher-order.md)**：二次手法が使えない理由と実践的な教訓
- **[LTC 回帰テスト](./ltc-regression.md)**：チューニング結果の長時間検証

## 参考文献

- James C. Spall (1992). "Multivariate Stochastic Approximation Using a Simultaneous Perturbation Gradient Approximation". *IEEE Transactions on Automatic Control*, 37(3), pp. 332-341.
- James C. Spall (1998). "An Overview of the Simultaneous Perturbation Method for Efficient Optimization". *Johns Hopkins APL Technical Digest*, 19(4), pp. 482-492.
- [Stockfish SPSA Tuning](https://www.chessprogramming.org/Stockfish%27s_Tuning_Method)

## 次に読む

→ **[勾配推定と摂動](./gradient.md)**：勾配推定の数学的な詳細と、整数パラメータの量子化の扱い。
