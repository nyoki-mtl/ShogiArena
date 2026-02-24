# SPSA（同時摂動確率近似法）

> **前提知識**: [Elo レーティング](../elo/index.md)、[SPRT](../sprt/index.md)

## このページの要点

- SPSA は**すべてのパラメータを同時に摂動**して勾配を推定する確率的最適化アルゴリズム
- パラメータが \\(p\\) 個あっても、1 回の更新にわずか **2 ゲームペア**で勾配を推定できる
- Rademacher 摂動（±1）を使い、各パラメータを ±step で同時に変化させる
- 将棋エンジンのパラメータチューニングに特に適しており、Stockfish の SPSA チューニングにも採用されている

## なぜ SPSA が必要か

将棋エンジンには、評価関数の重み、探索パラメータ、枝刈りの閾値など、数十〜数百のパラメータがあります。
これらを最適化するには、以下の課題があります。

1. **目的関数が解析的でない**: 勝率は閉形式で表現できず、実際に対局しないと評価できない
2. **評価にコストがかかる**: 1 回の評価（対局）に数秒〜数分かかる
3. **ノイズが大きい**: 対局結果は確率的であり、同じパラメータでも結果がばらつく
4. **パラメータ数が多い**: 個別に 1 パラメータずつ最適化するのは非効率

SPSA はこれらすべての課題に対応するアルゴリズムです。

### 他のアプローチとの比較

| 手法 | 1 回あたりの評価数 | ノイズ耐性 | 適用条件 |
|:---|:---:|:---:|:---|
| グリッドサーチ | \\(n^p\\) | 低い | パラメータ数が少ない場合のみ |
| 有限差分法 | \\(2p\\) | 中 | パラメータ数が少ない場合 |
| **SPSA** | **2** | **高い** | **パラメータ数に依存しない** |
| ベイズ最適化 | 1 | 高い | サロゲートモデルの構築が可能な場合 |

SPSA の最大の強みは、パラメータ数 \\(p\\) に関わらず **2 回の評価で勾配を推定できる**ことです。

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

**目的関数**: \\(L(\boldsymbol{\theta})\\) = パラメータ \\(\boldsymbol{\theta}\\) での期待勝率

**更新式**: 反復 \\(k\\) における各パラメータ \\(i\\) の更新:

\\[
\theta_i^{(k+1)} = \theta_i^{(k)} + a_k \cdot \delta_i \cdot \frac{s^+ - s^-}{2}
\\]

ここで:
- \\(a_k\\): ステップサイズゲイン（[ゲインスケジュール](./gain-schedule.md)で詳述）
- \\(\delta_i\\): パラメータ固有の学習率スケール
- \\(s^+, s^-\\): 摂動パラメータでの対局スコア

## Rademacher 摂動

SPSA の核心は**同時摂動**です。すべてのパラメータを同時に ±1 で摂動します。

\\[
\varepsilon_i \sim \text{Rademacher} = \begin{cases} +1 & \text{確率} \; 1/2 \\\\ -1 & \text{確率} \; 1/2 \end{cases}
\\]

```python
C = [0.0 if p.not_used else
     (p.step * (1.0 if rng.randint(0, 1) else -1.0))
     for p in params]
```

### なぜ Rademacher 分布か

Spall (1992) の理論により、摂動分布が以下を満たす必要があります:[^spall-1992]

1. 平均ゼロ: \\(E[\varepsilon_i] = 0\\)
2. 有界な逆モーメント: \\(E[1/\varepsilon_i^2] < \infty\\)

正規分布は条件 2 を満たしません（\\(1/\varepsilon\\) の分散が発散）。
Rademacher 分布は \\(1/\varepsilon_i^2 = 1\\) なので条件 2 を自動的に満たし、最も単純かつ効率的です。

## 勾配の推定

同時摂動による勾配推定の式:

\\[
\hat{g}_i = \frac{L(\boldsymbol{\theta} + c_k \boldsymbol{\varepsilon}) - L(\boldsymbol{\theta} - c_k \boldsymbol{\varepsilon})}{2 c_k \varepsilon_i}
\\]

**有限差分法との比較**:

```text
有限差分法: パラメータ i だけを ±c で変化させて勾配を推定
  → 2p 回の評価が必要（p パラメータの場合）
  → 各パラメータごとに独立に推定

SPSA: すべてのパラメータを同時に ±c·ε で変化させて勾配を推定
  → 2 回の評価で十分
  → ノイズが大きいが、反復平均で収束
```

有限差分法では \\(2p\\) 回の評価が必要ですが、SPSA ではわずか 2 回です。
この「\\(p\\) に依存しない」性質が、多パラメータ最適化における SPSA の最大の強みです。

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
    # 1. Rademacher 摂動の生成
    rng = self._make_rng(int(update_idx))
    C = [p.step * (1.0 if rng.randint(0, 1) else -1.0)
         for p in params]

    # 2. ゲインの計算
    a_k = a0 / ((A + k) ** alpha)
    c_k = c0 / (k ** gamma)

    # 3. パラメータの摂動
    tuned_plus  = [p.v + c_k * c for p, c in zip(params, C)]
    tuned_minus = [p.v - c_k * c for p, c in zip(params, C)]

    # 4. ゲームペアの実行（CRN あり/なし）
    s_plus  = await self._run_game_pair(sfen, tuned_plus, ...)
    s_minus = await self._run_game_pair(sfen, tuned_minus, ...)

    # 5. パラメータ更新
    step_factor = (s_plus - s_minus) / 2.0
    for i, p in enumerate(params):
        delta_theta = mobility * p.delta * step_factor * C[i]
        p.v = quantize_value(p, p.v + delta_theta)
```

## 設定例

```yaml
# SPSA 設定
num_updates: 1000         # 更新回数
mobility: 0.5             # 全体の学習率スケール
scale: 1.0                # 摂動スケール

# ゲインスケジュール
a0: 1.0                   # 初期ステップサイズ
A: 100.0                  # スケーリング定数
alpha: 0.602              # ステップサイズ減衰指数
gamma: 0.101              # 摂動スケール減衰指数

# 分散削減
crn_enabled: true          # Common Random Number
update_batch_size: 4       # バッチサイズ

# 並列実行
inflight_factor: 8         # 同時ゲーム数の乗数

# Early Stopping
early_stop:
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
| `arena/orchestrators/spsa_orchestrator.py` | `SpsaOrchestrator` | SPSA 実行の本体 |
| `arena/orchestrators/spsa_orchestrator.py` | `_run_one_spsa_update()` | 1 回の更新サイクル |
| `arena/tuning/param_io.py` | `ParamEntry` | パラメータ定義 |
| `arena/tuning/param_io.py` | `read_params()` / `write_params()` | パラメータの I/O |
| `arena/tuning/param_io.py` | `quantize_value()` | 値の量子化・クランプ |
| `arena/configs/spsa.py` | `SpsaRunConfig` | SPSA 設定スキーマ |
| `arena/runners/spsa_runner.py` | `SpsaRunner` | SPSA ランナー |

## 読了順序

- **[勾配推定と摂動](./gradient.md)** — 勾配推定の数学的詳細と量子化の扱い
- **[ゲインスケジュール](./gain-schedule.md)** — 収束を制御する減衰系列の設計
- **[ノイズと高次手法の限界](./noise-and-higher-order.md)** — なぜ二次手法が使えないのか、実践的な教訓
- **[LTC 回帰テスト](./ltc-regression.md)** — チューニング結果の長時間検証

## 参考文献

[^spall-1992]: James C. Spall (1992). "Multivariate Stochastic Approximation Using a Simultaneous Perturbation Gradient Approximation". *IEEE Transactions on Automatic Control*, 37(3), pp. 332-341.
[^spall-1998]: James C. Spall (1998). "An Overview of the Simultaneous Perturbation Method for Efficient Optimization". *Johns Hopkins APL Technical Digest*, 19(4), pp. 482-492.
[^stockfish-spsa]: [Stockfish SPSA Tuning](https://www.chessprogramming.org/Stockfish%27s_Tuning_Method) — Chess Programming Wiki のチューニング手法解説

## 次に読む

→ **[勾配推定と摂動](./gradient.md)**: 勾配推定の数学的な詳細と、整数パラメータの量子化の扱いを解説します。
