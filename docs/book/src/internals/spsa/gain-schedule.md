# ゲインスケジュール

> **前提知識**：[SPSA](./index.md)、[勾配推定と摂動](./gradient.md)

## このページの要点

- ゲインスケジュールは SPSA の収束速度と安定性を制御する **2 つの系列** \\(c_i\\)（摂動の大きさ）と
  実効ステップゲイン \\(a_{k,i}\\) で構成される
- ShogiArena の実装は **パラメータ単位** の系列を `compute_classic_schedule_point`
  （`_core/contexts/spsa/application/classic_schedule.py`）で算出する。グローバルな学習率スカラ
  （かつての `mobility` / `a0`）は存在しない
- 系列は反復番号ではなく **ペア通し番号** \\(k_{\text{pair}}\\)（バッチ境界）で進む
- 既定値は Spall の推奨減衰（\\(\alpha = 0.602,\ \gamma = 0.101\\)）。\\(A\\) は初期の不安定性を
  緩和するウォームアップ項

## 系列の進み方（ペア通し番号）

更新は `pairs_per_update`（= \\(m\\)）ペアのバッチ単位で進みます。1-based の更新番号 \\(u\\) と
総更新回数 \\(N\\) = `num_updates` に対して:

\\[
k_{\text{pair}} = u \cdot m \qquad k_{\text{total}} = N \cdot m
\\]

\\(A\\) は `mode` に応じて絶対値かペア総数に対する比率として解釈されます。

\\[
A_{\text{abs}} =
\begin{cases}
A_{\text{value}} \cdot k_{\text{total}} & (\text{mode} = \text{ratio}) \\\\
A_{\text{value}} & (\text{mode} = \text{absolute})
\end{cases}
\\]

## 2 つのゲイン系列（パラメータ単位）

各パラメータ \\(i\\) は、空間定義（space spec）からの摂動基準 \\(\text{step}_i\\) と学習率係数
\\(\delta_i\\) を持ちます。

### 摂動スケール \\(c_i\\)

\\[
c_i = \text{step}_i \cdot \left(\frac{k_{\text{total}}}{k_{\text{pair}}}\right)^{\gamma}
\\]

\\(c_i\\) は勾配推定のために \\(\theta_i\\) を動かす幅です。
\\(k_{\text{pair}}\\) が進む（後半になる）ほど減衰し、最終更新（\\(k_{\text{pair}} = k_{\text{total}}\\)）で
\\(c_i = \text{step}_i\\) に落ち着きます。
整数パラメータは量子化で摂動が消えないよう、`int_ck_floor` で下限クランプされます。

### 更新ゲイン \\(r_i\\)

\\[
r_i = \frac{\delta_i \cdot \text{step}_i^{2} \cdot (A_{\text{abs}} + k_{\text{total}})^{\alpha}}
{(A_{\text{abs}} + k_{\text{pair}})^{\alpha} \cdot c_i^{2}}
\\]

パラメータ更新は次式です（\\(\text{step} = \sum s^{+} - \sum s^{-}\\) はバッチのスコア集計、
\\(\text{flip}_i \in \{+1, -1\}\\) は摂動の向き）。

\\[
\Delta\theta_i = r_i \cdot c_i \cdot \text{step} \cdot \text{flip}_i
\\]

勾配推定 \\(\hat{g}_i = \text{step} / (2 c_i \text{flip}_i)\\) を代入すると、更新は
\\(\Delta\theta_i = 2\, r_i\, c_i^{2}\, \hat{g}_i\\) と書け、\\(i\\) の **実効ステップゲイン** は

\\[
a_{k,i} = 2\, r_i\, c_i^{2}
= \frac{2\, \delta_i \cdot \text{step}_i^{2} \cdot (A_{\text{abs}} + k_{\text{total}})^{\alpha}}
{(A_{\text{abs}} + k_{\text{pair}})^{\alpha}}
\\]

すなわち \\(a_{k,i} = a_i / (A_{\text{abs}} + k_{\text{pair}})^{\alpha}\\) という古典 SPSA の形であり、
グローバルな \\(a_0\\) ではなくパラメータ単位の \\(\delta_i\\) / \\(\text{step}_i\\) が定数項を決めます。

### パラメータの意味

| パラメータ | 意味 | 既定値 | Spall 推奨 |
|:---:|:---|:---:|:---:|
| \\(\text{step}_i\\) | パラメータ \\(i\\) の摂動基準（space spec） | パラメータ依存 | 問題依存 |
| \\(\delta_i\\) | パラメータ \\(i\\) の学習率係数（space spec） | パラメータ依存 | 問題依存 |
| \\(A\\) | ウォームアップ定数（`absolute` 値または `ratio`） | `absolute` 0.0 | \\(\approx 0.1\,k_{\text{total}}\\) |
| \\(\alpha\\) | ステップゲイン減衰指数 | 0.602 | 0.602 |
| \\(\gamma\\) | 摂動スケール減衰指数 | 0.101 | 0.101 |

## 実装

```python
# _core/contexts/spsa/application/classic_schedule.py
k_pair  = update_idx * pairs_per_update
k_total = num_updates * pairs_per_update
A_abs   = a_value * k_total if a_mode == "ratio" else a_value

for param in params:                      # is_not_used は除外
    c_i = param.step * (k_total ** gamma) / (k_pair ** gamma)
    if param.type == "int":
        c_i = max(c_i, int_ck_floor)
    r_i = (
        param.delta * (param.step ** 2)
        * ((A_abs + k_total) ** alpha)
        / (((A_abs + k_pair) ** alpha) * (c_i ** 2))
    )
```

```python
# _core/contexts/spsa/adapters/orchestrator_update_flow.py（更新ステップ）
new_value = param.value + r_i * c_i * step * flip
```

## \\(A\\) パラメータの役割

最適化の序盤は、まだパラメータが最適値から遠く、しかも勾配推定の精度も低い状態です。
ここで満額のステップを踏むとパラメータが大きく振れてしまうため、\\(A\\) を分母に足して
\\(k_{\text{pair}}\\) が小さいうちの実効ステップゲインを抑えます。

\\[
a_{k,i} = \frac{a_i}{(A_{\text{abs}} + k_{\text{pair}})^{\alpha}}
\\]

- \\(A_{\text{abs}} = 0\\): 最初のバッチから完全なゲインで開始
- \\(A_{\text{abs}}\\) を大きく: 序盤のゲインを抑え、後半に効かせる

Spall の推奨は \\(A_{\text{abs}} \approx 0.1 \cdot k_{\text{total}}\\)（ペア総数の 10%）で、これは
`A.mode = ratio`, `A.value = 0.1` に対応します。

## 設定ガイド

### 調整の順序

1. **既定の減衰から開始**：\\(\alpha = 0.602,\ \gamma = 0.101\\)、`A.mode = absolute`, `A.value = 0`
2. **`step` / `delta` をパラメータごとに調整**：1 更新でパラメータが過大に動かないか確認する
3. **必要に応じてウォームアップを導入**：序盤が不安定なら `A` を増やす（`ratio` の場合 0.1 程度）

### 推奨設定パターン

| ケース | \\(\alpha\\) | \\(\gamma\\) | \\(A\\) | 説明 |
|:---|:---:|:---:|:---:|:---|
| 既定（理論的最適） | 0.602 | 0.101 | absolute 0 | Spall の漸近最適レート |
| 安定（ウォームアップ強め） | 0.602 | 0.101 | ratio 0.1 | 序盤のゲインを抑制 |
| 緩やかな減衰 | 0.51 | 0.01 | absolute 0 | 現行契約の範囲内で減衰を最小限にする |

### `step` / `delta`（パラメータ固有スケール）の目安

更新幅と摂動はパラメータ単位の `step` / `delta` で制御します（グローバルスカラはありません）。

```text
高感度パラメータ（小さな変化で勝率に大きく影響）:
  → delta を小さく / step を小さく

低感度パラメータ（大きな変化でも勝率への影響が小さい）:
  → delta を大きく / step を大きく
```

> **注**：`scale` は別概念です。`scaled_integer` エンコーディングでチューニング値を USI オプションの
> 整数値へ変換するための係数（`int(round(value * scale))`）であり、ゲインスケジュールの \\(c_0\\) では
> ありません。

## ゲインスケジュールの収束条件

ゲイン系列を減衰させる理由は、確率的近似の収束条件（Robbins-Monro 条件）にあります。
SPSA が確率 1 で収束するには、次が十分条件です。

\\[
\sum_{k=1}^{\infty} a_k = \infty \qquad \sum_{k=1}^{\infty} a_k^2 < \infty
\\]

\\[
c_k \to 0 \qquad \sum_{k=1}^{\infty} \frac{a_k^2}{c_k^2} < \infty
\\]

前半の 2 条件はステップが尽きずに動き続けることと分散が発散しないことを、後半は摂動が縮む速さと
ステップの縮む速さの釣り合いを要求します。
\\(\alpha = 0.602,\ \gamma = 0.101\\) は、これらを満たしたうえで漸近的な収束レートが最良になる値として
Spall が導出したものです。

現行の ShogiArena は収束条件を外れる固定ゲインを提供しません。
`algorithm.alpha` は \\(0.5 < \alpha \le 1.0\\)、`algorithm.gamma` は
\\(0 < \gamma \le 0.5\\) の範囲で指定します。

## 実装リファレンス

| ファイル | 設定キー / 関数 | 役割 |
|---------|---------|------|
| `_core/contexts/spsa/application/classic_schedule.py` | `compute_classic_schedule_point` | パラメータ単位の \\(c_i\\) / \\(r_i\\) 算出 |
| `_core/contexts/spsa/adapters/orchestrator_update_flow.py` | 更新ステップ | \\(\Delta\theta_i = r_i c_i \cdot \text{step} \cdot \text{flip}_i\\) |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `algorithm.alpha` | ステップゲイン減衰指数（既定 0.602） |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `algorithm.gamma` | 摂動スケール減衰指数（既定 0.101） |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `algorithm.A` | ウォームアップ定数（`mode` / `value`） |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `int_ck_floor` | 整数パラメータの摂動下限（既定 0.5） |

## 次に読む

→ **[ノイズと高次手法の限界](./noise-and-higher-order.md)**：二次手法（2SPSA、Newton 法、BFGS）がエンジンチューニングで使えない理由と、Fishtest コミュニティの実践的な知見。
