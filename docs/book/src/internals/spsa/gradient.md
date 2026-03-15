# 勾配推定と摂動

> **前提知識**: [SPSA](./index.md)（アルゴリズムの概要、Rademacher 摂動）

## このページの要点

- 同時摂動勾配推定は、すべてのパラメータを同時に摂動して **2 回の評価**で \\(p\\) 次元の勾配を推定する
- 推定勾配はノイズが大きいが、反復平均で真の勾配に収束する
- 整数パラメータは小数部を「積立」することで、小さな更新を蓄積させる
- `quantize_value()` がクランプ・丸め・ステップ整合を一括して処理する

## 同時摂動勾配推定の詳細

### 基本式

パラメータ \\(i\\) の勾配推定:

\\[
\hat{g}_i = \frac{L(\boldsymbol{\theta} + c_k \boldsymbol{\varepsilon}) - L(\boldsymbol{\theta} - c_k \boldsymbol{\varepsilon})}{2 c_k \varepsilon_i}
\\]

ShogiArena の実装では、\\(\varepsilon_i\\) は `step` でスケーリングされた Rademacher 摂動です。

```python
C = [p.step * (1.0 if rng.randint(0, 1) else -1.0)
     for p in params]
```

したがって \\(c_i = \text{step}_i \cdot (\pm 1)\\) であり、勾配推定は:

\\[
\hat{g}_i = \frac{s^+ - s^-}{2 \cdot c_k \cdot c_i}
\\]

### パラメータ更新式

実際の更新では、`delta` パラメータを学習率スケーリングとして使用します。

\\[
\Delta\theta_i = \text{mobility} \cdot \delta_i \cdot \frac{s^+ - s^-}{2} \cdot c_i
\\]

```python
step_factor = (s_plus - s_minus) / 2.0
for i, p in enumerate(params):
    if p.not_used:
        continue
    delta_theta = mobility * float(p.delta) * step_factor * C[i]
    new_v = p.v + delta_theta
    p.v = quantize_value(p, new_v)
```

> **注**: この更新式は標準的な SPSA 更新と同じ「2 点差分を使う一次更新」という枠組みに属しますが、
> 実装上は `delta` と `mobility` を明示的に導入し、パラメータごとのスケーリングを直接制御しています。

## スコアの計算

対局の結果は以下のようにスコアに変換されます。

| 結果 | スコア |
|:---:|:---:|
| 勝ち | 1.0 |
| 引き分け | 0.5 |
| 負け | 0.0 |

ペアゲーム（先後入れ替え）の場合、ペアの平均スコアを使用します。

### SPRT 用の結果正規化

SPSA オーケストレータ内で SPRT を併用する場合、結果をチューニング対象エンジンの視点に正規化します。

```python
def _ltc_normalize_result_for_sprt(
    result: GameResult, tuned_as_black: bool
) -> GameResult:
    # tuned_as_black=True: 結果をそのまま使用
    # tuned_as_black=False: 結果を反転（黒勝利 ↔ 白勝利）
```

## 整数パラメータの扱い

### 問題：小さな更新の消失

整数パラメータ（例: `NullMovePruning = 3`）の場合、更新量が 1 未満だと丸めによって消失します。

```text
θ = 3, Δθ = 0.3
  → round(3 + 0.3) = round(3.3) = 3  ← 更新が消失！
```

### 解決策：小数部の「積立」

ShogiArena では、整数パラメータでも内部的に**小数値を保持**します。
小さな更新が蓄積されて 1 を超えた時点で、エンジンに渡す値が変化します。

```text
反復 1: θ = 3.0, Δθ = 0.3 → θ = 3.3  (エンジンには 3)
反復 2: θ = 3.3, Δθ = 0.3 → θ = 3.6  (エンジンには 4)
反復 3: θ = 3.6, Δθ = 0.3 → θ = 3.9  (エンジンには 4)
反復 4: θ = 3.9, Δθ = 0.3 → θ = 4.2  (エンジンには 4)
```

```python
def quantize_value(e: ParamEntry, value: float,
                   snap_float=False, round_int=True):
    v = float(value)
    mn, mx = float(e.min), float(e.max)
    st = float(e.step or 0.0)

    if e.type == "int":
        if not round_int:
            # 小数部を保持（積立モード）
            return max(mn, min(mx, v))
        snapped = int(round(v))
        clamped = max(int(ceil(mn)), min(int(floor(mx)), snapped))
        return float(clamped)
    else:  # float
        if snap_float and st > 0:
            kf = round(v / st)
            v = kf * st
        return max(mn, min(mx, v))
```

### c_k の下限（int_ck_floor）

整数パラメータの摂動スケール \\(c_k\\) が小さくなりすぎると、摂動が 0 に丸められて勾配推定が不可能になります。

```python
if any(p.type == "int" and not p.not_used for p in params):
    c_k = max(c_k_raw, self.config.int_ck_floor)
else:
    c_k = c_k_raw
```

デフォルトの `int_ck_floor = 0.5` は、摂動が最低でも \\(\pm 0.5\\)（丸め後に ±1）になることを保証します。

## Float パラメータのステップ整合

`snap_float_to_step = true` の場合、float パラメータもステップの整数倍に整合されます。

```text
step = 0.1, v = 1.537
  → round(1.537 / 0.1) * 0.1 = round(15.37) * 0.1 = 15 * 0.1 = 1.5
```

これはエンジンが特定の粒度でしかパラメータを受け付けない場合に有用です。

## バッチ処理による分散削減

1 回の更新で複数のゲームペアを実行し、スコアを平均することで推定の分散を削減できます。

\\[
s^+_{\text{avg}} = \frac{1}{B} \sum_{b=1}^{B} s^+_b \qquad s^-_{\text{avg}} = \frac{1}{B} \sum_{b=1}^{B} s^-_b
\\]

```python
batch_size = self.config.update_batch_size or 1
total_s_plus = 0.0
total_s_minus = 0.0

for batch_idx in range(batch_size):
    s_plus_i, s_minus_i = await run_batch_item(batch_idx)
    total_s_plus += s_plus_i
    total_s_minus += s_minus_i

s_plus = total_s_plus / batch_size
s_minus = total_s_minus / batch_size
```

### バッチサイズの選び方

| バッチサイズ | 分散 | 計算コスト | 推奨用途 |
|:---:|:---:|:---:|:---|
| 1 | 高い | 低い | パラメータが少なく、早く探索したい |
| 4 | 中 | 中 | 一般的なチューニング |
| 8-16 | 低い | 高い | 安定した最適化が必要 |

バッチサイズを \\(B\\) にすると分散は \\(1/B\\) に減少しますが、計算コストは \\(B\\) 倍になります。

## 勾配推定の性質

### 不偏性

同時摂動勾配推定は、Rademacher 摂動の性質により**不偏推定量**です。

\\[
E[\hat{g}_i] = \frac{\partial L}{\partial \theta_i} + O(c_k^2)
\\]

\\(c_k \to 0\\) のとき、バイアスは消失します。

### 分散

推定の分散は \\(c_k\\) に反比例します。

\\[
\text{Var}[\hat{g}_i] \propto \frac{1}{c_k^2}
\\]

\\(c_k\\) を小さくするとバイアスは減りますが分散が増えます。
このトレードオフは [ゲインスケジュール](./gain-schedule.md) で制御します。

## 実装リファレンス

| ファイル | 関数 | 役割 |
|---------|------|------|
| `_core/contexts/spsa/adapters/orchestrator_update_mixin.py` | `_run_one_spsa_update()` | 勾配推定と更新の本体 |
| `_core/contexts/spsa/application/param_io.py` | `quantize_value()` | 値の量子化 |
| `_core/contexts/spsa/adapters/orchestrator_gameplay_mixin.py` | `_ltc_normalize_result_for_sprt()` | SPRT 用の結果正規化 |

## 次に読む

→ **[ゲインスケジュール](./gain-schedule.md)**: \\(a_k\\) と \\(c_k\\) の減衰系列による収束制御を解説します。
